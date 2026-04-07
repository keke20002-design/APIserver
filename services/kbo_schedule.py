import asyncio
import logging
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

NAVER_SPORTS_SCHEDULE_URL = (
    "https://api-gw.sports.naver.com/schedule/games"
)
NAVER_SPORTS_PREVIEW_URL = (
    "https://api-gw.sports.naver.com/schedule/games/{game_id}/preview"
)
NAVER_SPORTS_RELAY_URL = (
    "https://api-gw.sports.naver.com/schedule/games/{game_id}/relay"
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


PITCH_TYPE_KO = {
    "FAST": "직구",
    "TWOS": "투심",
    "CUTT": "커터",
    "SLID": "슬라이더",
    "CURV": "커브",
    "CHUP": "체인지업",
    "FORK": "포크",
    "SINK": "싱커",
    "KNUC": "너클볼",
}


def _parse_starter(starter: dict | None) -> dict | None:
    """Extract relevant starter info from preview API response."""
    if not starter:
        return None
    info = starter.get("playerInfo", {})
    season_stats = starter.get("currentSeasonStatsOnOpponents", {})
    pitch_kinds = starter.get("currentPitKindStats", [])

    # 구종 정보: type + speed + 비율
    pitches = [
        {
            "type": p.get("type", ""),
            "type_ko": PITCH_TYPE_KO.get(p.get("type", ""), p.get("type", "")),
            "speed": p.get("speed"),
            "ratio": round(p.get("pit_rt", 0), 1),
        }
        for p in pitch_kinds
        if p.get("type")
    ]

    return {
        "name": info.get("name", ""),
        "backnum": info.get("backnum", ""),
        "era": season_stats.get("era"),
        "wins": season_stats.get("w"),
        "losses": season_stats.get("l"),
        "pitches": pitches,
    }


async def _fetch_starters(client: httpx.AsyncClient, game_id: str) -> dict:
    """Fetch home/away starters from preview API. Returns {} on failure."""
    try:
        url = NAVER_SPORTS_PREVIEW_URL.format(game_id=game_id)
        response = await client.get(url, timeout=8.0)
        response.raise_for_status()
        data = response.json()
        preview = data.get("result", {}).get("previewData", {})
        return {
            "home_starter": _parse_starter(preview.get("homeStarter")),
            "away_starter": _parse_starter(preview.get("awayStarter")),
        }
    except Exception as e:
        logger.warning("Failed to fetch starters for game %s: %s", game_id, e)
        return {"home_starter": None, "away_starter": None}


async def _fetch_current_pitchers(client: httpx.AsyncClient, game_id: str) -> dict:
    """Fetch current pitchers from relay API for live games. Returns {} on failure."""
    try:
        url = NAVER_SPORTS_RELAY_URL.format(game_id=game_id)
        response = await client.get(url, timeout=8.0)
        response.raise_for_status()
        data = response.json()
        logger.debug("Relay API response for %s: %s", game_id, data)
        relay = data.get("result", {}).get("relayData", {})
        home_pitcher = relay.get("homePitcher", {}).get("name") if relay.get("homePitcher") else None
        away_pitcher = relay.get("awayPitcher", {}).get("name") if relay.get("awayPitcher") else None
        return {"home_current_pitcher": home_pitcher, "away_current_pitcher": away_pitcher}
    except Exception as e:
        logger.warning("Failed to fetch relay for game %s: %s", game_id, e)
        return {"home_current_pitcher": None, "away_current_pitcher": None}


async def fetch_kbo_schedule(target_date: date | None = None) -> list[dict]:
    """Fetch KBO game schedule for a given date from Naver Sports API."""
    if target_date is None:
        target_date = date.today()

    date_str = target_date.strftime("%Y-%m-%d")

    params = {
        "fields": "basic,schedule,baseball,manualRelayUrl",
        "upperCategoryId": "kbaseball",
        "fromDate": date_str,
        "toDate": date_str,
        "size": "500",
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://sports.naver.com/kbaseball/schedule/index",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            response = await client.get(
                NAVER_SPORTS_SCHEDULE_URL, params=params
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as e:
        logger.error("Failed to fetch KBO schedule: %s", e)
        raise RuntimeError(f"Failed to fetch KBO schedule: {e}")

    games = _parse_schedule(data, date_str)

    if games:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Fetch starters for all games in parallel
            starter_tasks = [
                _fetch_starters(client, g["game_id"])
                for g in games
                if g.get("game_id")
            ]
            starters_list = await asyncio.gather(*starter_tasks)

            # Fetch current pitchers only for live games
            live_games = [g for g in games if g.get("status") == "경기 중" and g.get("game_id")]
            if live_games:
                relay_tasks = [_fetch_current_pitchers(client, g["game_id"]) for g in live_games]
                relay_list = await asyncio.gather(*relay_tasks)
                relay_map = {g["game_id"]: r for g, r in zip(live_games, relay_list)}
            else:
                relay_map = {}

        for game, starters in zip(games, starters_list):
            game["home_starter"] = starters.get("home_starter")
            game["away_starter"] = starters.get("away_starter")
            relay = relay_map.get(game.get("game_id"), {})
            game["home_current_pitcher"] = relay.get("home_current_pitcher")
            game["away_current_pitcher"] = relay.get("away_current_pitcher")

    return games


def _parse_schedule(data: dict, date_str: str) -> list[dict]:
    """Parse Naver Sports API response into structured game list."""
    games = []

    items = data.get("result", {}).get("games", [])
    if not items:
        items = data.get("games", [])

    for game in items:
        game_id = game.get("gameId", "")
        home_team = (
            game.get("homeTeamName")
            or game.get("homeTeam", {}).get("name", "")
            or ""
        )
        away_team = (
            game.get("awayTeamName")
            or game.get("awayTeam", {}).get("name", "")
            or ""
        )
        game_time = game.get("gameDateTime") or game.get("startTime") or ""
        status_code = game.get("statusCode") or game.get("gameStatus") or ""

        home_score = (
            game.get("homeTeamScore")
            if game.get("homeTeamScore") is not None
            else game.get("homeScore")
        )
        away_score = (
            game.get("awayTeamScore")
            if game.get("awayTeamScore") is not None
            else game.get("awayScore")
        )

        stadium = game.get("stadiumName") or game.get("stadium") or ""

        display_time = ""
        if game_time:
            try:
                dt = datetime.fromisoformat(str(game_time).replace("Z", "+00:00"))
                display_time = dt.strftime("%H:%M")
            except (ValueError, AttributeError):
                display_time = str(game_time)

        game_status = _determine_game_status(status_code)

        if not stadium and home_team in TEAM_STADIUM:
            stadium = TEAM_STADIUM[home_team]

        ticket_url = ""
        for key, url in TICKET_LINKS.items():
            if key in stadium:
                ticket_url = url
                break

        score_str = None
        if game_status in ("경기 중", "경기 종료") and home_score is not None and away_score is not None:
            score_str = f"{away_score}:{home_score}"

        games.append({
            "game_id": game_id,
            "home_team": home_team,
            "away_team": away_team,
            "stadium": stadium,
            "time": display_time,
            "status": game_status,
            "score": score_str,
            "ticket_url": ticket_url,
            "date": date_str,
            # starters and current pitchers will be filled after parallel fetch
            "home_starter": None,
            "away_starter": None,
            "home_current_pitcher": None,
            "away_current_pitcher": None,
        })

    logger.info("Parsed %d KBO games for %s", len(games), date_str)
    return games


def _determine_game_status(status_code: str) -> str:
    """Convert API status code to display status."""
    status_map = {
        "BEFORE": "경기 전",
        "READY": "경기 전",
        "STARTED": "경기 중",
        "LIVE": "경기 중",
        "PLAYING": "경기 중",
        "RESULT": "경기 종료",
        "AFTER": "경기 종료",
        "FINAL": "경기 종료",
        "END": "경기 종료",
        "FINISH": "경기 종료",
        "CANCEL": "경기 취소",
        "POSTPONE": "경기 연기",
        "DELAY": "경기 연기",
    }
    return status_map.get(status_code, status_code or "미정")
