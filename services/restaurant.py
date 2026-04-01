import asyncio
import logging
from collections import Counter

from services.naver_api import search_blog, search_local

logger = logging.getLogger(__name__)

# 구장 주변 맛집 검색 키워드
STADIUM_FOOD_QUERIES = {
    "잠실": ["잠실야구장 맛집", "잠실종합운동장 먹거리"],
    "고척": ["고척돔 맛집", "고척스카이돔 먹거리"],
    "문학": ["인천문학경기장 맛집", "문학야구장 먹거리"],
    "수원": ["수원KT위즈파크 맛집", "수원야구장 먹거리"],
    "사직": ["사직야구장 맛집", "사직동 먹거리"],
    "대전": ["대전한화생명이글스파크 맛집", "대전야구장 먹거리"],
    "대구": ["대구삼성라이온즈파크 맛집", "대구야구장 먹거리"],
    "광주": ["광주기아챔피언스필드 맛집", "광주야구장 먹거리"],
    "창원": ["창원NC파크 맛집", "창원야구장 먹거리"],
}

# 리뷰 요약용 음식 키워드
FOOD_KEYWORDS = [
    "치킨", "맥주", "핫도그", "피자", "떡볶이", "순대", "소시지",
    "곱창", "족발", "삼겹살", "라면", "짜장면", "짬뽕", "돈까스",
    "햄버거", "감자튀김", "회", "초밥", "국밥", "비빔밥", "냉면",
    "갈비", "보쌈", "김밥", "떡갈비", "양념치킨", "후라이드",
]

# 리뷰 분위기 키워드
VIBE_KEYWORDS = {
    "positive": ["맛있", "추천", "최고", "대박", "굿", "좋아", "인기", "유명"],
    "negative": ["줄 길", "웨이팅", "비싸", "별로", "실망", "불친절"],
    "tip": ["주차", "예약", "웨이팅", "포장", "배달"],
}


async def search_restaurants(stadium: str) -> dict:
    """Search restaurants near a stadium and summarize reviews."""
    queries = STADIUM_FOOD_QUERIES.get(stadium)
    if not queries:
        # 범용 검색
        queries = [f"{stadium}야구장 맛집"]

    # 1) 지역 검색으로 맛집 리스트
    local_results = await search_local(queries[0], display=5)

    # 2) 블로그 검색으로 리뷰 수집 (모든 쿼리 동시 실행)
    blog_tasks = [search_blog(q, display=10) for q in queries]
    blog_results_lists = await asyncio.gather(*blog_tasks, return_exceptions=True)

    all_blog_texts = []
    for result in blog_results_lists:
        if isinstance(result, Exception):
            logger.warning("Blog search failed: %s", result)
            continue
        for item in result:
            all_blog_texts.append(item["text"])

    # 3) 리뷰 텍스트 분석
    popular_menus = _extract_popular_menus(all_blog_texts)
    review_summary = _summarize_reviews(all_blog_texts)

    return {
        "stadium": stadium,
        "restaurants": local_results,
        "popular_menus": popular_menus,
        "review_summary": review_summary,
        "total_reviews_analyzed": len(all_blog_texts),
    }


def _extract_popular_menus(texts: list[str]) -> list[dict]:
    """Extract popular menu items from review texts by frequency."""
    counter = Counter()
    for text in texts:
        for keyword in FOOD_KEYWORDS:
            count = text.count(keyword)
            if count > 0:
                counter[keyword] += count

    return [
        {"menu": menu, "mentions": count}
        for menu, count in counter.most_common(5)
    ]


def _summarize_reviews(texts: list[str]) -> dict:
    """Summarize review sentiment and extract key phrases."""
    positive_count = 0
    negative_count = 0
    tips = []

    for text in texts:
        for kw in VIBE_KEYWORDS["positive"]:
            positive_count += text.count(kw)
        for kw in VIBE_KEYWORDS["negative"]:
            negative_count += text.count(kw)
        for kw in VIBE_KEYWORDS["tip"]:
            if kw in text:
                # 해당 키워드 포함 문장 추출
                for sentence in text.split("."):
                    if kw in sentence and len(sentence.strip()) < 80:
                        tips.append(sentence.strip())

    # 중복 제거 후 상위 3개 팁
    unique_tips = list(dict.fromkeys(tips))[:3]

    total = positive_count + negative_count
    if total == 0:
        sentiment = "정보 부족"
    elif positive_count / total >= 0.7:
        sentiment = "매우 긍정적"
    elif positive_count / total >= 0.5:
        sentiment = "긍정적"
    else:
        sentiment = "호불호 있음"

    return {
        "sentiment": sentiment,
        "positive_mentions": positive_count,
        "negative_mentions": negative_count,
        "tips": unique_tips,
    }
