import logging

from services.daejeon_parking_api import fetch_daejeon_parking_lots, STADIUM_KEYWORDS
from services.seoul_parking_api import fetch_parking_lots
from services.schedule_estimator import estimate_parking_status

logger = logging.getLogger(__name__)

# 구장별 데이터 소스 라우팅 테이블
# "seoul"    → 서울 열린데이터 실시간 API
# "daejeon"  → 대전광역시 실시간 주차 API
# "schedule" → 경기 일정 기반 추정 (해당 지자체 API 확보 전 임시)
STADIUM_SOURCE = {
    "잠실": "seoul",
    "고척": "seoul",
    "문학": "schedule",  # TODO: 인천시 실시간 주차 API 연동 예정
    "수원": "schedule",  # TODO: 경기도 실시간 주차 API 연동 예정
    "사직": "schedule",  # TODO: 부산시 실시간 주차 API 연동 예정
    "대전": "daejeon",
    "대구": "schedule",  # TODO: 대구시 실시간 주차 API 연동 예정
    "광주": "schedule",  # TODO: 광주시 실시간 주차 API 연동 예정
    "창원": "schedule",  # TODO: 창원시 실시간 주차 API 연동 예정
}

QUE_STATUS_MAP = {"10": "good", "20": "normal", "30": "bad"}

STATUS_MESSAGES = {
    "good": "현재 주차 여유",
    "normal": "현재 주차 보통",
    "bad": "현재 주차 혼잡",
    "unknown": "주차 정보 없음",
}


def _aggregate_status(lots: list[dict]) -> str:
    """Determine overall status from filtered lots.

    Prefers occupancy-rate calculation when capacity data is available;
    falls back to QUE_STATUS majority vote otherwise.
    """
    lots_with_capacity = [l for l in lots if l["total"] > 0]

    if lots_with_capacity:
        total_current = sum(l["current"] for l in lots_with_capacity)
        total_capacity = sum(l["total"] for l in lots_with_capacity)
        rate = total_current / total_capacity
        if rate < 0.5:
            return "good"
        elif rate < 0.8:
            return "normal"
        else:
            return "bad"

    statuses = [
        QUE_STATUS_MAP[l["que_status"]]
        for l in lots
        if l["que_status"] in QUE_STATUS_MAP
    ]
    if not statuses:
        return "unknown"
    return max(set(statuses), key=statuses.count)


def _aggregate_status_by_remaining(lots: list[dict]) -> str:
    """Determine status using remaining (resQty) ratio.

    Uses totalQty / remaining from Daejeon API format.
    """
    valid = [l for l in lots if l["total"] > 0]
    if not valid:
        return "unknown"

    total_capacity = sum(l["total"] for l in valid)
    total_remaining = sum(l["remaining"] for l in valid)
    availability = total_remaining / total_capacity

    if availability > 0.5:
        return "good"
    elif availability > 0.2:
        return "normal"
    else:
        return "bad"


async def _analyze_daejeon(location: str) -> dict:
    """Real-time analysis using Daejeon Open Data parking API."""
    try:
        lots = await fetch_daejeon_parking_lots(keywords=STADIUM_KEYWORDS)
    except Exception as e:
        logger.error("Daejeon parking API failed for '%s': %s", location, e)
        return {
            "location": location,
            "status": "unknown",
            "message": STATUS_MESSAGES["unknown"],
            "lots": [],
            "estimated": False,
        }

    if not lots:
        logger.warning("No Daejeon parking lots found for keywords %s", STADIUM_KEYWORDS)
        return {
            "location": location,
            "status": "unknown",
            "message": STATUS_MESSAGES["unknown"],
            "lots": [],
            "estimated": False,
        }

    status = _aggregate_status_by_remaining(lots)

    lots_out = [
        {
            "name": l["name"],
            "status": _remaining_to_status(l),
            "current": l["current"],
            "total": l["total"],
            "remaining": l["remaining"],
            "occupancy_rate": (
                round(l["current"] / l["total"], 2) if l["total"] > 0 else None
            ),
            "address": l["address"],
        }
        for l in lots
    ]

    return {
        "location": location,
        "status": status,
        "message": STATUS_MESSAGES.get(status, STATUS_MESSAGES["unknown"]),
        "lots": lots_out,
        "estimated": False,
    }


def _remaining_to_status(lot: dict) -> str:
    if lot["total"] == 0:
        return "unknown"
    availability = lot["remaining"] / lot["total"]
    if availability > 0.5:
        return "good"
    elif availability > 0.2:
        return "normal"
    else:
        return "bad"


async def _analyze_seoul(location: str) -> dict:
    """Real-time analysis using Seoul Open Data parking API."""
    try:
        all_lots = await fetch_parking_lots()
    except Exception as e:
        logger.error("Seoul parking API failed for '%s': %s", location, e)
        return {
            "location": location,
            "status": "unknown",
            "message": STATUS_MESSAGES["unknown"],
            "lots": [],
            "estimated": False,
        }

    filtered = [l for l in all_lots if location in l["name"]]

    if not filtered:
        logger.warning("No Seoul parking lots found for '%s'", location)
        return {
            "location": location,
            "status": "unknown",
            "message": STATUS_MESSAGES["unknown"],
            "lots": [],
            "estimated": False,
        }

    status = _aggregate_status(filtered)

    lots_out = [
        {
            "name": l["name"],
            "status": QUE_STATUS_MAP.get(l["que_status"], "unknown"),
            "current": l["current"],
            "total": l["total"],
            "occupancy_rate": (
                round(l["current"] / l["total"], 2) if l["total"] > 0 else None
            ),
        }
        for l in filtered
    ]

    return {
        "location": location,
        "status": status,
        "message": STATUS_MESSAGES.get(status, STATUS_MESSAGES["unknown"]),
        "lots": lots_out,
        "estimated": False,
    }


async def analyze(location: str) -> dict:
    """Analyze parking congestion for a given stadium location.

    Routes to the appropriate data source based on STADIUM_SOURCE registry.
    """
    source = STADIUM_SOURCE.get(location)

    if source is None:
        logger.warning("Unknown stadium '%s', falling back to schedule estimation", location)
        return await estimate_parking_status(location)

    if source == "seoul":
        return await _analyze_seoul(location)

    if source == "daejeon":
        return await _analyze_daejeon(location)

    # "schedule" and any future unmapped source → schedule-based estimation
    return await estimate_parking_status(location)
