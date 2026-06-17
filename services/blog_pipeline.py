"""블로그 자동 포스팅 파이프라인 — 뉴스 수집 → 해설형 콘텐츠 생성 → WordPress 발행."""
import asyncio
import logging
import os
from datetime import datetime

import pytz

from services.blog_db import init_db, mark_keyword_used, save_post_log, get_recent_posts
from services.blog_naver_service import collect_hot_news, pick_fallback_keyword
from services.blog_gemini_service import generate_analytical_post, generate_blog_post, generate_english_post, generate_post_image
from services.blog_wordpress_service import publish_post, upload_image, edit_post

logger = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")

# 하루 최대 발행 수 (한국어+영어 슬롯당 2개 × 4슬롯 = 8, 여유 2)
MAX_POSTS_PER_DAY = 10

# 시간대별 카테고리 우선순위
SLOT_CATEGORY_HINT: dict[str, str] = {
    "morning": "AI",
    "afternoon": "AI",
    "evening": "AI",
}

_MAX_RETRIES = 3
_RETRY_DELAY = 5


_BLOG_CSS = """<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
.entry-content,.post-content{font-family:'Pretendard',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:760px;margin-left:auto;margin-right:auto;}
.entry-content p,.post-content p,p{line-height:1.9;margin-bottom:28px;}
.entry-content h2,.post-content h2,h2{font-size:34px;line-height:1.4;margin-top:60px;margin-bottom:24px;font-weight:800;border-left:5px solid #2563eb;padding-left:15px;}
.entry-content strong,.post-content strong,strong{color:#2563eb;font-style:normal;}
.summary-box{background:#f8fafc;border-left:5px solid #2563eb;padding:20px;border-radius:12px;margin:25px 0;}
.summary-box h3{margin:0 0 10px;font-size:15px;color:#1e40af;}
.summary-box ul{margin:0;padding-left:18px;}
.summary-box li{padding:3px 0;color:#1f2937;font-size:15px;line-height:1.6;}
.quote-box{padding:20px;background:#f8fafc;border-left:4px solid #2563eb;margin:30px 0;border-radius:10px;font-size:16px;line-height:1.8;color:#1f2937;}
.point-box{background:#eff6ff;padding:18px;border-radius:10px;font-weight:600;margin:20px 0;color:#1e3a8a;font-size:15px;line-height:1.7;}
.stat-grid{display:flex;gap:15px;margin:25px 0;flex-wrap:wrap;}
.stat-item{flex:1;min-width:90px;padding:20px;background:#f8fafc;border-radius:12px;text-align:center;}
.stat-item h3{margin:0 0 4px;font-size:22px;color:#2563eb;font-weight:800;}
.stat-item p{margin:0;font-size:13px;color:#6b7280;}
.my-opinion{background:#fefce8;border-left:4px solid #f59e0b;padding:18px 20px;border-radius:0 10px 10px 0;margin:24px 0;}
.my-opinion h3{margin:0 0 8px;font-size:14px;color:#92400e;}
.my-opinion p{margin:0;color:#1f2937;font-size:15px;line-height:1.7;}
.checklist{background:#f0fdf4;border:1px solid #bbf7d0;padding:18px 20px;border-radius:12px;margin:24px 0;}
.checklist h3{margin:0 0 10px;font-size:15px;color:#15803d;}
.checklist ul{margin:0;padding-left:18px;}
.checklist li{padding:4px 0;color:#1f2937;font-size:14px;}
.conclusion-box{background:#1e3a8a;color:white;padding:24px;border-radius:14px;margin:28px 0;}
.conclusion-box h3{margin:0 0 10px;font-size:16px;color:#bfdbfe;}
.conclusion-box p{margin:0;font-size:15px;line-height:1.8;}
.faq-item{margin:16px 0;}
.faq-q{font-size:16px;font-weight:700;color:#1e3a8a;margin-bottom:6px;}
.faq-a{color:#374151;line-height:1.8;}
</style>\n"""


def _inject_summary_box(content: str, summary: list[str]) -> str:
    """본문 최상단에 CSS + 3줄 요약 박스 삽입."""
    if not summary:
        return _BLOG_CSS + content
    items_html = "".join(f"<li>{item}</li>" for item in summary[:3])
    box = (
        '<div class="summary-box">'
        "<h3>📌 3줄 요약</h3>"
        f"<ul>{items_html}</ul>"
        "</div>\n"
    )
    return _BLOG_CSS + box + content


def _inject_adsense(content: str) -> str:
    """두 번째 </h2> 뒤에 인아티클 광고 삽입. 슬롯 ID 없으면 Auto Ads에 위임."""
    pub_id = os.getenv("ADSENSE_PUB_ID", "ca-pub-6848418595819302")
    slot_id = os.getenv("ADSENSE_AD_SLOT", "")
    if not slot_id:
        return content  # Auto Ads가 자동 처리

    ad_html = (
        '<div style="text-align:center;margin:32px 0;">'
        '<ins class="adsbygoogle"'
        ' style="display:block;text-align:center;"'
        ' data-ad-layout="in-article"'
        ' data-ad-format="fluid"'
        f' data-ad-client="{pub_id}"'
        f' data-ad-slot="{slot_id}"></ins>'
        '<script>(adsbygoogle = window.adsbygoogle || []).push({});</script>'
        '</div>'
    )

    # 두 번째 </h2> 뒤에 삽입
    idx = content.find("</h2>")
    if idx != -1:
        idx2 = content.find("</h2>", idx + 1)
        insert_pos = (idx2 + 5) if idx2 != -1 else (idx + 5)
        return content[:insert_pos] + "\n" + ad_html + "\n" + content[insert_pos:]
    return content + "\n" + ad_html


def get_current_slot() -> str:
    hour = datetime.now(KST).hour
    if 5 <= hour < 11:
        return "morning"
    elif 11 <= hour < 17:
        return "afternoon"
    else:
        return "evening"


def _count_today_posts() -> int:
    today = datetime.now(KST).strftime("%Y-%m-%d")
    posts = get_recent_posts(limit=20)
    return sum(
        1 for p in posts
        if p.get("created_at", "").startswith(today) and p.get("status") != "failed"
    )


async def _run_with_retry(coro_fn, retries: int = _MAX_RETRIES, delay: int = _RETRY_DELAY):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn()
        except Exception as e:
            last_err = e
            logger.warning("Attempt %d/%d failed: %s", attempt, retries, e)
            if attempt < retries:
                await asyncio.sleep(delay * attempt)
    raise last_err


async def run_blog_pipeline(
    slot: str | None = None,
    wp_status: str = "draft",
    force: bool = False,
) -> dict:
    """
    전체 파이프라인 실행.
    - slot: morning | afternoon | evening (None이면 현재 시간 기준 자동)
    - wp_status: draft | publish | private
    - force: 하루 최대 발행 수 무시
    """
    init_db()

    chosen_slot = slot or get_current_slot()

    # 하루 최대 발행 수 체크
    if not force:
        today_count = _count_today_posts()
        if today_count >= MAX_POSTS_PER_DAY:
            raise RuntimeError(
                f"오늘 이미 {today_count}개 발행됨. 하루 최대 {MAX_POSTS_PER_DAY}개 제한."
            )

    logger.info("=== Blog pipeline start — slot=%s ===", chosen_slot)

    # 1. 핫 뉴스 수집
    hot_issue = await _run_with_retry(lambda: collect_hot_news(chosen_slot))

    if hot_issue and hot_issue.get("news"):
        news_items = hot_issue["news"]
        category = hot_issue.get("category", SLOT_CATEGORY_HINT.get(chosen_slot, "AI"))
        logger.info("News collected — query='%s', items=%d", hot_issue["query"], len(news_items))

        # 2. 한국어 + 영어 콘텐츠 동시 생성
        _ni, _sl, _cat = news_items, chosen_slot, category
        post_data, en_post_data = await asyncio.gather(
            _run_with_retry(lambda: generate_analytical_post(_ni, _sl)),
            _run_with_retry(lambda: generate_english_post(_ni, _sl, _cat)),
        )
        keyword = post_data["keyword"]

    else:
        # 폴백: 뉴스 없으면 키워드 기반 생성
        logger.warning("No hot news found, falling back to keyword-based generation")
        keyword = await pick_fallback_keyword(chosen_slot)
        if not keyword:
            raise RuntimeError("뉴스 수집 및 키워드 수집 모두 실패")
        from services.blog_naver_service import _detect_category as _naver_cat
        category = _naver_cat(keyword)
        _kw, _cat = keyword, category
        post_data, en_post_data = await asyncio.gather(
            _run_with_retry(lambda: generate_blog_post(_kw, _cat)),
            _run_with_retry(lambda: generate_english_post([], chosen_slot, _cat, keyword=_kw)),
        )
        post_data["keyword"] = keyword

    title = post_data["title"]
    summary = post_data.get("summary", [])
    tags = post_data.get("tags", [])
    meta_description = post_data.get("meta_description", "")

    # 요약 박스 → 광고 삽입 순서로 content 구성
    content = _inject_summary_box(post_data["content"], summary)
    content = _inject_adsense(content)

    # 영어 포스트 content 구성
    en_title = en_post_data["title"]
    en_summary = en_post_data.get("summary", [])
    en_tags = en_post_data.get("tags", [])
    en_meta_description = en_post_data.get("meta_description", "")
    en_content = _inject_summary_box(en_post_data["content"], en_summary)
    en_content = _inject_adsense(en_content)

    # 3. 대표 이미지 생성 — 인라인 삽입 + WordPress Featured Image 설정
    featured_image_id: int | None = None
    try:
        img_result = await generate_post_image(keyword, title)
        if img_result:
            img_bytes, img_mime = img_result
            ext = "jpg" if "jpeg" in img_mime else img_mime.split("/")[-1]
            filename = f"post-{chosen_slot}-{datetime.now(KST).strftime('%Y%m%d%H%M%S')}.{ext}"
            img_upload = await upload_image(img_bytes, img_mime, filename)
            img_url = img_upload.get("url", "")
            raw_id = img_upload.get("id")
            if raw_id:
                try:
                    featured_image_id = int(raw_id)
                except (ValueError, TypeError):
                    pass
                logger.info("Post image uploaded (featured only) — url=%s, attachment_id=%s", img_url, featured_image_id)
    except Exception as e:
        logger.warning("Image step failed, continuing without image: %s", e)

    # 5. WordPress 발행
    try:
        wp_result = await _run_with_retry(
            lambda: publish_post(
                title=title,
                content=content,
                tags=tags,
                category=category,
                meta_description=meta_description,
                status=wp_status,
                featured_image_id=featured_image_id,
            )
        )
    except Exception as e:
        save_post_log(keyword, category, title, status="failed", error_msg=str(e))
        logger.error("WordPress publish failed: %s", e)
        raise

    wp_post_id = wp_result["post_id"]
    wp_url = wp_result["url"]

    # 5-1. 트래킹 픽셀 삽입 (발행 후 post_id 확보 후 편집)
    api_base = os.getenv("API_BASE_URL", "").rstrip("/")
    if api_base:
        tracking_pixel = (
            f'\n<img src="{api_base}/blog/track/{wp_post_id}" '
            f'width="1" height="1" style="display:none" alt="">'
        )
        try:
            await edit_post(wp_post_id, {"post_content": content + tracking_pixel})
            logger.info("Tracking pixel injected — post_id=%d", wp_post_id)
        except Exception as e:
            logger.warning("Tracking pixel inject failed: %s", e)

    # 5-2. 영어 포스트 발행
    en_wp_post_id: int | None = None
    en_wp_url = ""
    try:
        en_wp_result = await _run_with_retry(
            lambda: publish_post(
                title=en_title,
                content=en_content,
                tags=en_tags,
                category=category,
                meta_description=en_meta_description,
                status=wp_status,
                featured_image_id=featured_image_id,
            )
        )
        en_wp_post_id = en_wp_result["post_id"]
        en_wp_url = en_wp_result["url"]

        if api_base:
            en_tracking_pixel = (
                f'\n<img src="{api_base}/blog/track/{en_wp_post_id}" '
                f'width="1" height="1" style="display:none" alt="">'
            )
            try:
                await edit_post(en_wp_post_id, {"post_content": en_content + en_tracking_pixel})
                logger.info("EN tracking pixel injected — post_id=%d", en_wp_post_id)
            except Exception as e:
                logger.warning("EN tracking pixel inject failed: %s", e)

        en_keyword = en_post_data.get("keyword", keyword)
        save_post_log(
            keyword=en_keyword,
            topic=f"{category}_en",
            title=en_title,
            wp_post_id=en_wp_post_id,
            wp_url=en_wp_url,
            status=en_wp_result["status"],
        )
        logger.info("EN post published — post_id=%d url=%s", en_wp_post_id, en_wp_url)
    except Exception as e:
        logger.warning("English post pipeline failed (KR post unaffected): %s", e)

    # 6. 로그 저장
    mark_keyword_used(keyword, category)
    save_post_log(
        keyword=keyword,
        topic=category,
        title=title,
        wp_post_id=wp_post_id,
        wp_url=wp_url,
        status=wp_result["status"],
    )

    result = {
        "slot": chosen_slot,
        "category": category,
        "keyword": keyword,
        "title": title,
        "tags": tags,
        "meta_description": meta_description,
        "wp_post_id": wp_post_id,
        "wp_url": wp_url,
        "wp_status": wp_result["status"],
        "en_title": en_title,
        "en_wp_post_id": en_wp_post_id,
        "en_wp_url": en_wp_url,
    }
    logger.info("=== Blog pipeline done — post_id=%s ===", wp_post_id)
    return result
