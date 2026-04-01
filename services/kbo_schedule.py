import logging
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

NAVER_SPORTS_SCHEDULE_URL = (
    "https://api-gw.sports.naver.com/schedule/games"
)

# 구장별 티켓 예매 링크
TICKET_LINKS = {
    "잠실": "https://www.ticketlink.co.kr/sports/baseball/59",
    "고척": "https://www.ticketlink.co.kr/sports/baseball/60",
    "문학": "https://www.ticketlink.co.kr/sports/baseball/61",
    "수원": "https://www.ticketlink.co.kr/sports/baseball/62",
    "사직": "https://www.ticketlink.co.kr/sports/baseball/63",
    "대전": "https://www.ticketlink.co.kr/sports/baseball/64",
    "대구": "https://www.ticketlink.co.kr/sports/baseball/65",
    "광주": "https://www.ticketlink.co.kr/sports/baseball/66",
    "창원": "https://www.ticketlink.co.kr/sports/baseball/67",
}

# 팀명 → 구장 매핑
TEAM_STADIUM = {
    "LG": "잠실",
    "두산": "잠실",
    "키움": "고척",
    "SSG": "문학",
    "KT": "수원",
    "롯데": "사직",
    "한화": "대전",
    "삼성": "대구",
    "KIA": "광주",
    "NC": "창원",
}


async def fetch_kbo_schedule(target_date: date | None = None) -> list[dict]:
    """Fetch KBO game schedule for a given date from Naver Sports API."""
    if target_date is None:
        target_date = date.today()

    date_str = target_date.strftime("%Y-%m-%d")

    params = {
        "fields": "basic,superCategoryId,categoryName",
        "upperCategoryId": "kbaseball",
        "categoryId": "kbo",
        "fromDate": date_str,
        "toDate": date_str,
        "roundCodes": "",
        "size": "500",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                NAVER_SPORTS_SCHEDULE_URL, params=params
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as e:
        logger.error("Failed to fetch KBO schedule: %s", e)
        raise RuntimeError(f"Failed to fetch KBO schedule: {e}")

    return _parse_schedule(data, date_str)


def _parse_schedule(data: dict, date_str: str) -> list[dict]:
    """Parse Naver Sports API response into structured game list."""
    games = []

    items = data.get("result", {}).get("games", [])
    if not items:
        # Fallback: try alternative response shapes
        items = data.get("games", [])

    for game in items:
        home_team = game.get("homeTeamName", "")
        away_team = game.get("awayTeamName", "")
        game_time = game.get("gameDateTime", "")
        status_code = game.get("statusCode", "")
        home_score = game.get("homeTeamScore", "")
        away_score = game.get("awayTeamScore", "")
        stadium = game.get("stadiumName", "")

        # 시간 파싱
        display_time = ""
        if game_time:
            try:
                dt = datetime.fromisoformat(game_time.replace("Z", "+00:00"))
                display_time = dt.strftime("%H:%M")
            except (ValueError, AttributeError):
                display_time = game_time

        # 경기 상태 결정
        game_status = _determine_game_status(status_code)

        # 구장 이름으로 티켓 링크 매칭
        ticket_url = ""
        for key, url in TICKET_LINKS.items():
            if key in stadium:
                ticket_url = url
                break
        # 홈팀으로 폴백
        if not ticket_url and home_team in TEAM_STADIUM:
            stadium_key = TEAM_STADIUM[home_team]
            ticket_url = TICKET_LINKS.get(stadium_key, "")

        games.append({
            "home_team": home_team,
            "away_team": away_team,
            "stadium": stadium,
            "time": display_time,
            "status": game_status,
            "score": f"{away_score}:{home_score}" if home_score != "" else None,
            "ticket_url": ticket_url,
            "date": date_str,
        })

    logger.info("Parsed %d KBO games for %s", len(games), date_str)
    return games


def _determine_game_status(status_code: str) -> str:
    """Convert API status code to display status."""
    status_map = {
        "BEFORE": "경기 전",
        "STARTED": "경기 중",
        "RESULT": "경기 종료",
        "CANCEL": "경기 취소",
        "POSTPONE": "경기 연기",
    }
    return status_map.get(status_code, status_code or "미정")
