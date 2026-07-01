import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

NAVER_BLOG_SEARCH_URL = "https://openapi.naver.com/v1/search/blog.json"
NAVER_LOCAL_SEARCH_URL = "https://openapi.naver.com/v1/search/local.json"
NAVER_NEWS_SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"


def _get_credentials() -> tuple[str, str]:
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "NAVER_CLIENT_ID and NAVER_CLIENT_SECRET environment variables are required"
        )
    return client_id, client_secret


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _strip_hashtags(text: str) -> str:
    """#태그 제거 후 연속 공백 정리."""
    return re.sub(r"\s+", " ", re.sub(r"#\S+", "", text)).strip()


def _is_hashtag_spam(title: str) -> bool:
    """제목의 절반 이상이 해시태그이면 True (스팸성 포스트 필터)."""
    words = title.split()
    if not words:
        return False
    hashtag_count = sum(1 for w in words if w.startswith("#"))
    return hashtag_count / len(words) >= 0.5


async def search_blog(query: str, display: int = 10) -> list[dict]:
    """Search Naver blog and return list of items with cleaned text."""
    client_id, client_secret = _get_credentials()
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {
        "query": query,
        "display": display,
        "sort": "date",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            NAVER_BLOG_SEARCH_URL, headers=headers, params=params
        )
        response.raise_for_status()
        data = response.json()

    items = data.get("items", [])
    results = []
    for item in items:
        title = _strip_hashtags(_strip_html(item.get("title", "")))
        description = _strip_hashtags(_strip_html(item.get("description", "")))

        # 해시태그 도배 포스트 제외
        if _is_hashtag_spam(_strip_html(item.get("title", ""))):
            logger.debug("Hashtag spam filtered: %s", title[:40])
            continue

        # 해시태그 제거 후 제목이 너무 짧으면 제외
        if len(title) < 5:
            continue

        results.append({
            "title": title,
            "description": description,
            "text": f"{title} {description}",
            "link": item.get("link", ""),
        })

    logger.info("Naver blog search for '%s': %d results", query, len(results))
    return results


async def search_news(query: str, display: int = 10, sort: str = "date") -> list[dict]:
    """Naver 뉴스 검색 — 최신순으로 기사 반환."""
    client_id, client_secret = _get_credentials()
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {"query": query, "display": display, "sort": sort}

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(NAVER_NEWS_SEARCH_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

    results = []
    for item in data.get("items", []):
        title = _strip_html(item.get("title", "")).strip()
        description = _strip_html(item.get("description", "")).strip()
        if not title:
            continue
        results.append({
            "title": title,
            "description": description,
            "pub_date": item.get("pubDate", ""),
            "link": item.get("originallink") or item.get("link", ""),
        })

    logger.info("Naver news search for '%s': %d results", query, len(results))
    return results


async def search_local(query: str, display: int = 5) -> list[dict]:
    """Search Naver local places and return list of items."""
    client_id, client_secret = _get_credentials()
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    params = {
        "query": query,
        "display": display,
        "sort": "comment",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            NAVER_LOCAL_SEARCH_URL, headers=headers, params=params
        )
        response.raise_for_status()
        data = response.json()

    items = data.get("items", [])
    results = []
    for item in items:
        results.append({
            "title": _strip_html(item.get("title", "")),
            "category": item.get("category", ""),
            "address": item.get("address", ""),
            "road_address": item.get("roadAddress", ""),
            "link": item.get("link", ""),
        })

    logger.info("Naver local search for '%s': %d results", query, len(results))
    return results
