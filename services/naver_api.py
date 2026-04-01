import logging
import os
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

NAVER_BLOG_SEARCH_URL = "https://openapi.naver.com/v1/search/blog.json"
NAVER_LOCAL_SEARCH_URL = "https://openapi.naver.com/v1/search/local.json"


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
        text = _strip_html(item.get("title", "")) + " " + _strip_html(
            item.get("description", "")
        )
        results.append({"text": text, "link": item.get("link", "")})

    logger.info("Naver blog search for '%s': %d results", query, len(results))
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
