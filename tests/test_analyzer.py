import pytest
from unittest.mock import AsyncMock, patch

from services.analyzer import _aggregate_status, analyze


# --- _aggregate_status unit tests ---

def test_aggregate_occupancy_good():
    lots = [{"que_status": "30", "current": 100, "total": 300}]  # 33% → good
    assert _aggregate_status(lots) == "good"


def test_aggregate_occupancy_normal():
    lots = [{"que_status": "10", "current": 180, "total": 300}]  # 60% → normal
    assert _aggregate_status(lots) == "normal"


def test_aggregate_occupancy_bad():
    lots = [{"que_status": "10", "current": 270, "total": 300}]  # 90% → bad
    assert _aggregate_status(lots) == "bad"


def test_aggregate_occupancy_boundary_50():
    lots = [{"que_status": "20", "current": 150, "total": 300}]  # 50% → normal
    assert _aggregate_status(lots) == "normal"


def test_aggregate_occupancy_boundary_80():
    lots = [{"que_status": "20", "current": 240, "total": 300}]  # 80% → bad
    assert _aggregate_status(lots) == "bad"


def test_aggregate_fallback_que_status_majority():
    lots = [
        {"que_status": "30", "current": 0, "total": 0},
        {"que_status": "30", "current": 0, "total": 0},
        {"que_status": "10", "current": 0, "total": 0},
    ]
    assert _aggregate_status(lots) == "bad"


def test_aggregate_fallback_unknown():
    lots = [{"que_status": "", "current": 0, "total": 0}]
    assert _aggregate_status(lots) == "unknown"


def test_aggregate_multiple_lots():
    lots = [
        {"que_status": "10", "current": 400, "total": 500},  # 80%
        {"que_status": "10", "current": 100, "total": 500},  # 20%
    ]
    # combined: 500/1000 = 50% → normal
    assert _aggregate_status(lots) == "normal"


# --- Seoul API path (잠실/고척) ---

MOCK_SEOUL_LOTS = [
    {"name": "잠실종합운동장 주차장", "que_status": "30", "current": 450, "total": 500},
    {"name": "잠실야구장 주차장", "que_status": "20", "current": 60, "total": 100},
    {"name": "고척스카이돔 주차장", "que_status": "10", "current": 20, "total": 200},
]


@pytest.mark.asyncio
async def test_analyze_jamsil_uses_seoul_api():
    with patch(
        "services.analyzer.fetch_parking_lots",
        new=AsyncMock(return_value=MOCK_SEOUL_LOTS),
    ):
        result = await analyze("잠실")

    assert result["location"] == "잠실"
    assert result["estimated"] is False
    assert len(result["lots"]) == 2


@pytest.mark.asyncio
async def test_analyze_gocheok_uses_seoul_api():
    with patch(
        "services.analyzer.fetch_parking_lots",
        new=AsyncMock(return_value=MOCK_SEOUL_LOTS),
    ):
        result = await analyze("고척")

    assert result["estimated"] is False
    assert result["lots"][0]["name"] == "고척스카이돔 주차장"


@pytest.mark.asyncio
async def test_analyze_seoul_api_failure_returns_unknown():
    with patch(
        "services.analyzer.fetch_parking_lots",
        new=AsyncMock(side_effect=RuntimeError("API error")),
    ):
        result = await analyze("잠실")

    assert result["status"] == "unknown"
    assert result["lots"] == []


# --- Daejeon API path ---

MOCK_DAEJEON_LOTS = [
    {"name": "한화생명이글스파크 주차장", "total": 500, "remaining": 300, "current": 200, "address": "대전 중구", "lat": "", "lon": ""},
    {"name": "한화이글스 2주차장", "total": 200, "remaining": 30, "current": 170, "address": "대전 중구", "lat": "", "lon": ""},
]


@pytest.mark.asyncio
async def test_analyze_daejeon_uses_real_api():
    with patch(
        "services.analyzer.fetch_daejeon_parking_lots",
        new=AsyncMock(return_value=MOCK_DAEJEON_LOTS),
    ):
        result = await analyze("대전")

    assert result["estimated"] is False
    assert result["location"] == "대전"
    assert len(result["lots"]) == 2


@pytest.mark.asyncio
async def test_analyze_daejeon_lot_has_remaining_field():
    with patch(
        "services.analyzer.fetch_daejeon_parking_lots",
        new=AsyncMock(return_value=MOCK_DAEJEON_LOTS),
    ):
        result = await analyze("대전")

    assert "remaining" in result["lots"][0]
    assert "occupancy_rate" in result["lots"][0]


@pytest.mark.asyncio
async def test_analyze_daejeon_api_failure_returns_unknown():
    with patch(
        "services.analyzer.fetch_daejeon_parking_lots",
        new=AsyncMock(side_effect=RuntimeError("API error")),
    ):
        result = await analyze("대전")

    assert result["status"] == "unknown"
    assert result["estimated"] is False


@pytest.mark.asyncio
async def test_analyze_daejeon_no_lots_returns_unknown():
    with patch(
        "services.analyzer.fetch_daejeon_parking_lots",
        new=AsyncMock(return_value=[]),
    ):
        result = await analyze("대전")

    assert result["status"] == "unknown"


# --- Schedule estimation path (사직 etc.) ---

MOCK_GAMES_WITH_SAJIK = [
    {"stadium": "사직야구장", "time": "18:30", "status": "경기 전"},
]

MOCK_GAMES_EMPTY = []


@pytest.mark.asyncio
async def test_analyze_sajik_uses_schedule_estimation():
    with patch(
        "services.schedule_estimator.fetch_kbo_schedule",
        new=AsyncMock(return_value=MOCK_GAMES_EMPTY),
    ):
        result = await analyze("사직")

    assert result["estimated"] is True


@pytest.mark.asyncio
async def test_analyze_schedule_fetch_failure_returns_unknown():
    with patch(
        "services.schedule_estimator.fetch_kbo_schedule",
        new=AsyncMock(side_effect=RuntimeError("fail")),
    ):
        result = await analyze("광주")

    assert result["status"] == "unknown"
    assert result["estimated"] is True


@pytest.mark.asyncio
async def test_analyze_unknown_stadium_falls_back_to_schedule():
    with patch(
        "services.schedule_estimator.fetch_kbo_schedule",
        new=AsyncMock(return_value=MOCK_GAMES_EMPTY),
    ):
        result = await analyze("알수없는구장")

    assert result["estimated"] is True
