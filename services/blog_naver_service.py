"""네이버 뉴스 기반 핫이슈 수집 — 4점수 합산 주제 선정 알고리즘."""
import asyncio
import logging
import random
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx
import pytz

from services.naver_api import search_news
from services.blog_db import get_recent_keyword_set

logger = logging.getLogger(__name__)

KST = pytz.timezone("Asia/Seoul")

# ── 시간대별 후보 키워드 (plan.md: AI 70% / 투자 30%) ───────────────────────
# morning: AI 전문 키워드 우선
# afternoon_ai: 오후 AI 슬롯 (주 5일)
# afternoon_market: 오후 투자 슬롯 (주 2일, day%3==0)
# evening: AI 전문 키워드 우선
SLOT_QUERIES: dict[str, list[str]] = {
    "morning":          ["ChatGPT", "Gemini", "Claude Code", "Claude AI", "AI Agent", "MCP", "AI 자동화", "OpenAI", "Cursor", "엔비디아"],
    "afternoon_ai":     ["Claude Code", "Cursor", "AI Agent", "MCP", "AI 자동화", "Gemini", "ChatGPT", "OpenAI", "Claude AI"],
    "afternoon_market": ["ETF", "미국주식", "비트코인", "AI 수혜주", "반도체", "엔비디아"],
    "evening":          ["ChatGPT", "Gemini", "Claude AI", "Claude Code", "AI Agent", "MCP", "엔비디아", "반도체", "AI 수혜주", "비트코인"],
}

# ── 수익성 등급 (plan.md 우선순위 기준) ──────────────────────────────────────
KEYWORD_GRADE: dict[str, str] = {
    # AI — S등급
    "ChatGPT": "S", "Gemini": "S", "Claude AI": "S", "Claude Code": "S", "Claude": "S",
    "AI Agent": "S", "MCP": "S", "AI 자동화": "S", "OpenAI": "S", "Cursor": "S",
    # 투자 — S등급
    "엔비디아": "S", "반도체": "S", "AI 수혜주": "S", "미국주식": "S", "비트코인": "S", "ETF": "S",
    # A등급
    "AI": "A", "아이폰": "A", "갤럭시": "A",
}
_GRADE_SCORE = {"S": 100, "A": 60, "B": 20}

# ── 검색 의도 판별 키워드 ─────────────────────────────────────────────────────
_INTENT_WORDS = ["이유", "원인", "전망", "영향", "분석", "전략", "대응", "효과", "예측", "전문가", "시사점", "대책", "주목"]

# ── 카테고리 매핑 ─────────────────────────────────────────────────────────────
KEYWORD_CATEGORY: dict[str, str] = {
    "ChatGPT": "AI", "Gemini": "AI", "Claude AI": "AI", "Claude": "AI",
    "OpenAI": "AI", "Cursor": "AI", "AI": "AI", "아이폰": "AI", "갤럭시": "AI",
    "Claude Code": "Automation", "MCP": "Automation", "AI Agent": "Automation", "AI 자동화": "Automation",
    "엔비디아": "Market", "반도체": "Market", "AI 수혜주": "Market",
    "미국주식": "Market", "ETF": "Market", "비트코인": "Crypto",
}


# ── plan.md 점수 체계: 검색가능성40 / 돈수익30 / AI자동화20 / 장기가치10 ────

_PRIORITY_KEYWORDS = [
    "ChatGPT", "Claude", "Claude Code", "Gemini", "Cursor",
    "MCP", "AI Agent", "OpenAI", "Gemini API", "AI 자동화",
    "엔비디아", "AI 관련주", "비트코인", "ETF", "미국주식",
]
_MONEY_HIGH = ["비트코인", "ETF", "미국주식", "엔비디아", "AI 관련주", "반도체", "주식", "투자", "수익"]
_MONEY_MID  = ["ChatGPT", "Gemini", "Claude", "OpenAI", "API", "비용", "가격"]
_AI_HIGH    = ["Claude Code", "MCP", "AI Agent", "AI 자동화", "Cursor"]
_AI_MID     = ["ChatGPT", "Gemini", "Claude", "OpenAI", "Gemini API", "AI"]
_EVERGREEN  = ["사용법", "방법", "비교", "차이", "비용", "전망", "가이드", "입문", "정리", "해보니"]


def _searchability_score(query: str, items: list[dict]) -> float:
    """검색 가능성 (40점): 기사량 + 우선순위 키워드 여부."""
    base = len(items) / 20 * 60
    if any(kw in query for kw in _PRIORITY_KEYWORDS):
        base += 40
    return min(100, base)


def _money_score(query: str) -> float:
    """돈/수익 관련성 (30점)."""
    if any(kw in query for kw in _MONEY_HIGH):
        return 100
    if any(kw in query for kw in _MONEY_MID):
        return 60
    return 20


def _ai_score(query: str) -> float:
    """AI/자동화 관련성 (20점)."""
    if any(kw in query for kw in _AI_HIGH):
        return 100
    if any(kw in query for kw in _AI_MID):
        return 80
    return 20


def _longtail_score(query: str) -> float:
    """장기 검색 가치 (10점): 에버그린 키워드 여부."""
    return 100 if any(kw in query for kw in _EVERGREEN) else 50


def _count_today_yesterday(items: list[dict]) -> tuple[int, int]:
    """pubDate 기준 오늘/어제 기사 수 반환."""
    today = datetime.now(KST).date()
    yesterday = today - timedelta(days=1)
    today_cnt = yesterday_cnt = 0
    for item in items:
        pub_str = item.get("pub_date", "")
        if not pub_str:
            continue
        try:
            dt = parsedate_to_datetime(pub_str).astimezone(KST).date()
            if dt == today:
                today_cnt += 1
            elif dt == yesterday:
                yesterday_cnt += 1
        except Exception:
            pass
    return today_cnt, yesterday_cnt


def _freshness_score(today_cnt: int, yesterday_cnt: int) -> float:
    """신선도 점수: 어제 대비 오늘 상승률 (최대 100)."""
    if yesterday_cnt == 0:
        return 100 if today_cnt > 0 else 0
    ratio = (today_cnt - yesterday_cnt) / yesterday_cnt
    return min(100, max(0, ratio * 100))


def _has_recent_duplicate(query: str, recent_kws: set[str]) -> bool:
    """최근 7일 내 포스팅된 키워드와 중복 여부 (부분 매칭)."""
    q = query.lower()
    return any(q in kw for kw in recent_kws)


_GUIDES_SUFFIXES = ("사용법", "활용법", "방법", "튜토리얼", "비교", "가이드")

# plan.md 절대 생성 금지 — 뉴스 제목에 이 패턴이 많으면 해당 후보 제외
_BANNED_TITLE_PATTERNS = (
    "오늘의 AI 뉴스", "AI 뉴스 모음", "AI 업계 소식",
    "오늘의 경제 뉴스", "뉴스 브리핑", "일일 요약",
    "뉴스레터", "주간 뉴스", "이번 주 뉴스",
)

def _is_banned_topic(items: list[dict]) -> bool:
    """뉴스 제목 다수가 금지 패턴이면 True (기사량 기반 포스팅 차단)."""
    if not items:
        return False
    banned = sum(
        1 for item in items
        if any(p in item.get("title", "") for p in _BANNED_TITLE_PATTERNS)
    )
    return banned / len(items) > 0.5


def _detect_category(query: str) -> str:
    for kw, cat in KEYWORD_CATEGORY.items():
        if kw in query:
            return cat
    if any(s in query for s in _GUIDES_SUFFIXES):
        return "Guides"
    return "AI"


# ── 메인 수집 함수 ────────────────────────────────────────────────────────────

async def collect_hot_news(slot: str) -> Optional[dict]:
    """
    시간대별 후보 키워드를 4점수(기사량·검색의도·수익성·신선도) 합산으로 평가,
    최고 점수 주제 1개를 반환.
    반환: {"query": str, "category": str, "news": list[dict], "scores": dict}
    """
    # 오후 슬롯: 70% AI / 30% 투자 — 날짜 % 3 == 0이면 투자, 나머지는 AI
    if slot == "afternoon":
        from datetime import date as _date
        is_market_day = _date.today().day % 3 == 0
        queries = SLOT_QUERIES["afternoon_market"] if is_market_day else SLOT_QUERIES["afternoon_ai"]
    else:
        queries = SLOT_QUERIES.get(slot, SLOT_QUERIES["evening"])

    # 병렬 뉴스 수집 (display=20 — 신선도 계산에 충분한 양)
    tasks = [search_news(q, display=20, sort="date") for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    news_map: dict[str, list[dict]] = {}
    for q, r in zip(queries, results):
        if isinstance(r, list) and r:
            news_map[q] = r

    if not news_map:
        logger.warning("No news collected for slot '%s'", slot)
        return None

    max_count = max(len(items) for items in news_map.values())
    recent_kws = get_recent_keyword_set(days=7)

    scored = []
    for query, items in news_map.items():
        search_s  = _searchability_score(query, items)
        money_s   = _money_score(query)
        ai_s      = _ai_score(query)
        long_s    = _longtail_score(query)

        final = (
            search_s * 0.4
            + money_s  * 0.3
            + ai_s     * 0.2
            + long_s   * 0.1
        )

        duplicate = _has_recent_duplicate(query, recent_kws)
        if duplicate:
            final *= 0.7

        banned = _is_banned_topic(items)
        if banned:
            final = 0

        scored.append({
            "query":    query,
            "category": _detect_category(query),
            "news":     items[:8],
            "coverage": len(items),
            "scores": {
                "searchability": round(search_s, 1),
                "money":         round(money_s, 1),
                "ai":            round(ai_s, 1),
                "longtail":      round(long_s, 1),
                "final":         round(final, 1),
                "duplicate_penalty": duplicate,
                "banned":        banned,
            },
        })

    scored.sort(key=lambda x: x["scores"]["final"], reverse=True)
    best = scored[0]

    logger.info(
        "Topic selected — slot=%s query='%s' score=%.1f "
        "[search=%.1f money=%.1f ai=%.1f long=%.1f dup=%s]",
        slot, best["query"], best["scores"]["final"],
        best["scores"]["searchability"], best["scores"]["money"],
        best["scores"]["ai"], best["scores"]["longtail"],
        best["scores"]["duplicate_penalty"],
    )
    for rank, c in enumerate(scored[1:4], 2):
        logger.debug(
            "  #%d '%s' score=%.1f [search=%.1f money=%.1f ai=%.1f long=%.1f dup=%s]",
            rank, c["query"], c["scores"]["final"],
            c["scores"]["searchability"], c["scores"]["money"],
            c["scores"]["ai"], c["scores"]["longtail"],
            c["scores"]["duplicate_penalty"],
        )

    return best


# ── 폴백: 뉴스 없을 때 키워드 직접 선택 ─────────────────────────────────────
FALLBACK_SEEDS: dict[str, list[str]] = {
    "morning": [
        "Claude Code 자동화 방법", "ChatGPT 유료 무료 차이", "Gemini API 비용 정리",
        "MCP 서버 구축 방법", "AI Agent 만드는 방법", "Cursor 사용해보니",
    ],
    "afternoon": [
        "OpenAI API 사용 방법", "Claude Code vs Cursor 차이", "엔비디아 주가 전망",
        "비트코인 ETF 영향", "미국주식 AI 관련주", "ETF 투자 방법",
    ],
    "evening": [
        "Gemini 2.5 Flash 써보니", "ChatGPT 최신 기능 정리", "AI 자동화 사례",
        "반도체 주가 전망", "Claude 사용 비용 정리", "AI 수혜주 전망",
    ],
}


async def pick_fallback_keyword(slot: str) -> Optional[str]:
    from services.blog_db import is_keyword_used
    seeds = FALLBACK_SEEDS.get(slot, FALLBACK_SEEDS["evening"])
    random.shuffle(seeds)
    for seed in seeds:
        if not is_keyword_used(seed):
            return seed
    return seeds[0]
