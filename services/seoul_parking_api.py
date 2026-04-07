import logging
import os
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

SEOUL_PARKING_URL = "http://openapi.seoul.go.kr:8088/{key}/xml/GetParkingInfo/1/1000/"


async def fetch_parking_lots() -> list[dict]:
    """Fetch all real-time parking lots from Seoul Open Data Plaza API."""
    api_key = os.getenv("SEOUL_API_KEY")
    if not api_key:
        raise RuntimeError("SEOUL_API_KEY environment variable is required")

    url = SEOUL_PARKING_URL.format(key=api_key)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()

    root = ET.fromstring(response.text)

    lots = []
    for row in root.findall(".//row"):
        name = row.findtext("PKNM", "").strip()
        que_status = row.findtext("QUE_STATUS", "").strip()
        current = int(row.findtext("CUR_PK_CNT", "0") or 0)
        total = int(row.findtext("TP_PK_CNT", "0") or 0)
        lots.append({
            "name": name,
            "que_status": que_status,
            "current": current,
            "total": total,
        })

    logger.info("Seoul parking API: fetched %d lots", len(lots))
    return lots
