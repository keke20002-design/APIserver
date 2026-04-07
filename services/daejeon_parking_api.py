import asyncio
import logging
import math
import os
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

DAEJEON_PARKING_URL = "https://apis.data.go.kr/6300000/pis/parkinglotIF"
PAGE_SIZE = 50

# 한화이글스파크(대전야구장) 관련 주차장 검색 키워드
STADIUM_KEYWORDS = ["한화", "이글스", "한밭"]


def _parse_items(xml_text: str) -> tuple[list[dict], int]:
    """Parse XML response. Returns (items, totalCount)."""
    root = ET.fromstring(xml_text)
    total_count = int(root.findtext(".//totalCount") or 0)

    items = []
    for item in root.findall(".//item"):
        name = (item.findtext("name") or "").strip()
        total = int(item.findtext("totalQty") or 0)
        remaining = int(item.findtext("resQty") or 0)
        address = (item.findtext("address") or "").strip()
        lat = item.findtext("lat") or ""
        lon = item.findtext("lon") or ""

        items.append({
            "name": name,
            "total": total,
            "remaining": remaining,
            "current": max(0, total - remaining),
            "address": address,
            "lat": lat,
            "lon": lon,
        })

    return items, total_count


async def _fetch_page(client: httpx.AsyncClient, api_key: str, page: int) -> str:
    params = {
        "serviceKey": api_key,
        "numOfRows": PAGE_SIZE,
        "pageNo": page,
    }
    response = await client.get(DAEJEON_PARKING_URL, params=params)
    response.raise_for_status()
    return response.text


async def fetch_daejeon_parking_lots(keywords: list[str] | None = None) -> list[dict]:
    """Fetch Daejeon parking lots, filtered by keywords if provided.

    Paginates through all 471 lots using parallel requests.
    Returns lots whose name contains any of the given keywords.
    If keywords is None or empty, returns all lots.
    """
    api_key = os.getenv("DAEJEON_API_KEY")
    if not api_key:
        raise RuntimeError("DAEJEON_API_KEY environment variable is required")

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Fetch first page to get totalCount
        first_page_xml = await _fetch_page(client, api_key, 1)
        first_items, total_count = _parse_items(first_page_xml)

        all_items = list(first_items)

        # Fetch remaining pages in parallel
        total_pages = math.ceil(total_count / PAGE_SIZE)
        if total_pages > 1:
            tasks = [_fetch_page(client, api_key, p) for p in range(2, total_pages + 1)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("Daejeon parking page fetch failed: %s", result)
                    continue
                items, _ = _parse_items(result)
                all_items.extend(items)

    logger.info("Daejeon parking API: fetched %d lots (total: %d)", len(all_items), total_count)

    if not keywords:
        return all_items

    filtered = [
        lot for lot in all_items
        if any(kw in lot["name"] for kw in keywords)
    ]
    logger.info("Filtered to %d lots matching keywords %s", len(filtered), keywords)
    return filtered
