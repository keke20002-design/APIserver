import logging
from datetime import datetime, timezone, timedelta

from services.kbo_schedule import fetch_kbo_schedule

logger = logging.getLogger(__name__)

# KST = UTC+9
KST = timezone(timedelta(hours=9))


async def estimate_parking_status(stadium: str) -> dict:
    """Estimate parking congestion based on today's KBO game schedule.

    Logic:
    - No game today              → good
    - Game exists, off-peak      → normal
    - Within 2h before ~ 1h after game start → bad
    """
    try:
        games = await fetch_kbo_schedule()
    except Exception as e:
        logger.error("Schedule fetch failed for '%s': %s", stadium, e)
        return _result(stadium, "unknown", [], estimated=True)

    stadium_games = [g for g in games if stadium in g.get("stadium", "")]

    if not stadium_games:
        return _result(stadium, "good", [], estimated=True)

    now = datetime.now(KST)
    status = "normal"

    for game in stadium_games:
        game_time_str = game.get("time", "")
        if not game_time_str:
            continue
        try:
            game_dt = now.replace(
                hour=int(game_time_str[:2]),
                minute=int(game_time_str[3:5]),
                second=0,
                microsecond=0,
            )
            delta = (game_dt - now).total_seconds() / 60  # minutes

            # -60 ~ +120 minutes window: peak congestion
            if -60 <= delta <= 120:
                status = "bad"
                break
        except (ValueError, IndexError):
            continue

    return _result(stadium, status, stadium_games, estimated=True)


def _result(stadium: str, status: str, games: list, estimated: bool) -> dict:
    STATUS_MESSAGES = {
        "good": "현재 주차 여유",
        "normal": "현재 주차 보통",
        "bad": "현재 주차 혼잡",
        "unknown": "주차 정보 없음",
    }
    return {
        "location": stadium,
        "status": status,
        "message": STATUS_MESSAGES.get(status, "주차 정보 없음"),
        "lots": [],
        "estimated": estimated,  # Flutter 앱에서 "추정값" 표시용
        "games_today": len(games),
    }
