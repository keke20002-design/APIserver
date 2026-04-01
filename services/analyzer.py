import asyncio
import logging

from services.naver_api import search_blog

logger = logging.getLogger(__name__)

NEGATIVE_KEYWORDS = ["만차", "혼잡", "막힘", "주차불가"]
POSITIVE_KEYWORDS = ["여유", "널널", "자리있음"]

SEARCH_TEMPLATES = [
    "{location} 주차장 혼잡",
    "{location} 주차 만차",
    "{location} 주차 가능",
]

STATUS_MESSAGES = {
    "good": "현재 주차 여유",
    "normal": "현재 주차 보통",
    "bad": "현재 주차 혼잡",
}


def _score_text(text: str) -> int:
    """Score a single text based on keyword matching."""
    score = 0
    for kw in NEGATIVE_KEYWORDS:
        score += text.count(kw) * 2
    for kw in POSITIVE_KEYWORDS:
        score -= text.count(kw) * 1
    return score


def _determine_status(score: int) -> str:
    if score <= 2:
        return "good"
    elif score <= 6:
        return "normal"
    else:
        return "bad"


async def analyze(location: str) -> dict:
    """Analyze parking congestion for a given location."""
    queries = [t.format(location=location) for t in SEARCH_TEMPLATES]

    # Fetch all queries concurrently
    tasks = [search_blog(query) for query in queries]
    results_lists = await asyncio.gather(*tasks, return_exceptions=True)

    all_items = []
    for result in results_lists:
        if isinstance(result, Exception):
            logger.warning("Search query failed: %s", result)
            continue
        all_items.extend(result)

    total_score = sum(_score_text(item["text"]) for item in all_items)
    total_score = max(0, total_score)

    total_results = len(all_items)
    confidence = round(min(1.0, total_results / 30), 2)

    status = _determine_status(total_score)

    return {
        "location": location,
        "status": status,
        "score": total_score,
        "confidence": confidence,
        "message": STATUS_MESSAGES[status],
    }
