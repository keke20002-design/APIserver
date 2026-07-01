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
NAVER_SPORTS_RECORD_URL = (
    "https://api-gw.sports.naver.com/schedule/games/{game_id}/record"
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


def _parse_hitter_lineup(hitters: list) -> list[dict]:
    """Parse batter list from relay/record API into structured lineup.

    relay API 필드: battingOrder, h, so, position
    record battersBoxscore 필드: batOrder, hit, kk, pos, hra
    """
    lineup = []
    for h in hitters:
        name = h.get("name", "")
        if not name:
            continue
        ab = int(h.get("ab") or 0)
        hits = int(h.get("h") or h.get("hit") or 0)
        hr = int(h.get("hr") or 0)
        rbi = int(h.get("rbi") or 0)
        bb = int(h.get("bb") or 0)
        so = int(h.get("so") or h.get("kk") or h.get("k") or 0)
        lineup.append({
            "order": h.get("battingOrder") or h.get("batOrder") or h.get("no"),
            "name": name,
            "position": h.get("posName") or h.get("position") or h.get("pos", ""),
            "ab": ab,
            "h": hits,
            "hr": hr,
            "rbi": rbi,
            "bb": bb,
            "k": so,
            "stat": f"{hits}/{ab}" if ab > 0 else "-",
            "season_avg": h.get("seasonHra") or h.get("hra"),
            "season_hr": h.get("seasonHr") or h.get("seasonHomerun"),
            "season_rbi": h.get("seasonRbi") or h.get("seasonRbim"),
        })
    return lineup


def _parse_team_standing(s: dict | None) -> dict | None:
    """Extract team standing info from preview API homeStandings/awayStandings."""
    if not s:
        return None
    return {
        "rank": s.get("rank"),
        "team": s.get("name", ""),
        "win": s.get("w"),
        "draw": s.get("d"),
        "lose": s.get("l"),
        "win_rate": str(s.get("wra", "")),
        "recent": [],  # previousGames에서 별도 채움
    }


def _parse_recent_games(games: list) -> list[str]:
    """Extract recent 5 game results (승/패/무) in chronological order (newest first)."""
    return [
        g["result"] for g in games[:5]
        if g.get("result") in ("승", "패", "무")
    ]


def _parse_starter(starter: dict | None) -> dict | None:
    """Extract relevant starter info from preview API response."""
    if not starter:
        return None
    info = starter.get("playerInfo", {})
    # currentSeasonStats = 시즌 전체 성적 (ERA/승패의 기준)
    # currentSeasonStatsOnOpponents = 오늘 상대팀 한정 (항상 0일 수 있음)
    season_stats = starter.get("currentSeasonStats") or starter.get("currentSeasonStatsOnOpponents") or {}
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

    hit_type = info.get("hitType", "")
    if "좌투" in hit_type:
        pitcher_hand = "좌완"
    elif "우투" in hit_type:
        pitcher_hand = "우완"
    else:
        pitcher_hand = ""

    return {
        "name": info.get("name", ""),
        "backnum": info.get("backnum", ""),
        "pitcher_hand": pitcher_hand,
        "era": season_stats.get("era"),
        "wins": season_stats.get("w"),
        "losses": season_stats.get("l"),
        "pitches": pitches,
    }


def _parse_watch_points(preview: dict) -> list[str]:
    """관전포인트 문자열 리스트 생성 (최대 5개)."""
    points = []

    # 주목선수 최근 5경기 성적
    for side, emoji in (("home", "🏠"), ("away", "✈️")):
        top = preview.get(f"{side}TopPlayer") or {}
        stats = top.get("recentFiveGamesStats") or {}
        name = stats.get("playerName", "")
        if not name:
            continue
        hra = stats.get("hra", "-")
        hr = stats.get("hr", 0)
        rbi = stats.get("rbi", 0)
        parts = [f"타율 {hra}"]
        if hr:
            parts.append(f"{hr}홈런")
        if rbi:
            parts.append(f"{rbi}타점")
        points.append(f"{emoji} {name} 최근 5경기 {' '.join(parts)}")

    # 시즌 맞대결
    vs = preview.get("seasonVsResult") or {}
    hw = vs.get("hw", 0)
    hl = vs.get("hl", 0)
    aw = vs.get("aw", 0)
    al = vs.get("al", 0)
    if hw + hl + aw + al > 0:
        points.append(f"⚔️ 시즌 맞대결 홈 {hw}승 {hl}패 · 원정 {aw}승 {al}패")

    # 홈/원정팀 최근 흐름 + 순위
    for side, emoji in (("home", "🏠"), ("away", "✈️")):
        prev_games = preview.get(f"{side}TeamPreviousGames", [])
        results = [g["result"] for g in prev_games[:5] if g.get("result") in ("승", "패", "무")]
        if not results:
            continue
        standing = preview.get(f"{side}Standings") or {}
        team_name = standing.get("name", "")
        rank = standing.get("rank")
        result_str = " ".join(results)
        rank_str = f" ({rank}위)" if rank else ""
        label = (
            f"{emoji} {team_name}{rank_str} 최근 {len(results)}G  {result_str}"
            if team_name else
            f"{emoji} 최근 {len(results)}G  {result_str}"
        )
        points.append(label)

    return points[:5]


def _parse_preview_lineup(raw: list) -> list[dict]:
    """Parse pre-game announced lineup from preview API."""
    lineup = []
    order = 1
    for p in raw:
        name = p.get("name") or p.get("playerName") or ""
        if not name:
            continue
        # 투수(position=1) 제외
        if str(p.get("position", "")) == "1":
            continue
        lineup.append({
            "order": order,
            "name": name,
            "backnum": p.get("backnum", ""),
            "position": p.get("positionName") or p.get("pos") or "",
            "ab": 0, "h": 0, "hr": 0, "rbi": 0, "bb": 0, "k": 0,
            "stat": "-",
            "season_avg": p.get("hra") or p.get("seasonHra"),
            "season_hr": p.get("hr") or p.get("seasonHr"),
            "season_rbi": p.get("rbi") or p.get("seasonRbi"),
        })
        order += 1
    return lineup


async def _fetch_starters(client: httpx.AsyncClient, game_id: str) -> dict:
    """Fetch home/away starters from preview API. Returns {} on failure."""
    try:
        url = NAVER_SPORTS_PREVIEW_URL.format(game_id=game_id)
        response = await client.get(url, timeout=8.0)
        response.raise_for_status()
        data = response.json()
        preview = (data.get("result") or {}).get("previewData") or {}
        home_standing = _parse_team_standing(preview.get("homeStandings"))
        away_standing = _parse_team_standing(preview.get("awayStandings"))
        home_recent = _parse_recent_games(preview.get("homeTeamPreviousGames", []))
        away_recent = _parse_recent_games(preview.get("awayTeamPreviousGames", []))
        if home_standing:
            home_standing["recent"] = home_recent
        if away_standing:
            away_standing["recent"] = away_recent

        # 경기 전 예고 라인업 (homeTeamLineUp.fullLineUp)
        def _extract_lineup(lineup_obj) -> list:
            if isinstance(lineup_obj, dict):
                return lineup_obj.get("fullLineUp") or []
            if isinstance(lineup_obj, list):
                return lineup_obj
            return []

        home_lineup_raw = _extract_lineup(preview.get("homeTeamLineUp"))
        away_lineup_raw = _extract_lineup(preview.get("awayTeamLineUp"))
        home_lineup = _parse_preview_lineup(home_lineup_raw)
        away_lineup = _parse_preview_lineup(away_lineup_raw)
        logger.debug("[preview 라인업] game=%s | 홈: %d명 | 원정: %d명",
                    game_id, len(home_lineup), len(away_lineup))

        return {
            "home_starter": _parse_starter(preview.get("homeStarter")),
            "away_starter": _parse_starter(preview.get("awayStarter")),
            "home_standing": home_standing,
            "away_standing": away_standing,
            "watch_points": _parse_watch_points(preview),
            "home_lineup": home_lineup,
            "away_lineup": away_lineup,
        }
    except Exception as e:
        logger.warning("Failed to fetch starters for game %s: %s", game_id, e)
        return {
            "home_starter": None, "away_starter": None,
            "home_standing": None, "away_standing": None,
            "watch_points": [],
            "home_lineup": [],
            "away_lineup": [],
        }



async def _fetch_result_pitchers(client: httpx.AsyncClient, game_id: str) -> dict:
    """Fetch winning/losing/save pitcher + final batting lineup from record API."""
    try:
        url = NAVER_SPORTS_RECORD_URL.format(game_id=game_id)
        response = await client.get(url, timeout=8.0)
        response.raise_for_status()
        data = response.json()
        record_data = data.get("result", {}).get("recordData", {})

        pitching_result = record_data.get("pitchingResult", [])
        winning_pitcher = None
        losing_pitcher = None
        save_pitcher = None

        for p in pitching_result:
            wls = p.get("wls", "")
            name = p.get("name", "")
            if wls == "W":
                winning_pitcher = name
            elif wls == "L":
                losing_pitcher = name
            elif wls == "S":
                save_pitcher = name

        # 타격 결과: battersBoxscore.home / .away
        batters_box = record_data.get("battersBoxscore", {})
        home_hitters = batters_box.get("home", [])
        away_hitters = batters_box.get("away", [])
        home_lineup = _parse_hitter_lineup(home_hitters)
        away_lineup = _parse_hitter_lineup(away_hitters)

        logger.info("[결과투수] game=%s | 승: %s | 패: %s | 세이브: %s | 홈라인업: %d명 | 원정라인업: %d명",
                    game_id, winning_pitcher or "미확인", losing_pitcher or "미확인",
                    save_pitcher or "없음", len(home_lineup), len(away_lineup))

        return {
            "winning_pitcher": winning_pitcher,
            "losing_pitcher": losing_pitcher,
            "save_pitcher": save_pitcher,
            "home_lineup": home_lineup,
            "away_lineup": away_lineup,
        }
    except Exception as e:
        logger.warning("Failed to fetch result pitchers for game %s: %s", game_id, e)
        return {
            "winning_pitcher": None,
            "losing_pitcher": None,
            "save_pitcher": None,
            "home_lineup": [],
            "away_lineup": [],
        }


async def _fetch_current_pitchers(client: httpx.AsyncClient, game_id: str) -> dict:
    """Fetch current pitchers and bullpen from relay API for live games. Returns {} on failure."""
    try:
        url = NAVER_SPORTS_RELAY_URL.format(game_id=game_id)
        response = await client.get(url, timeout=8.0)
        response.raise_for_status()
        data = response.json()
        result = data.get("result", {})
        relay = result.get("textRelayData", {})

        current_state = relay.get("currentGameState", {})
        current_pcode = str(current_state.get("pitcher", ""))

        # lineup.pitcher = 현재까지 등판한 투수 목록, pcode로 현재 투수 매칭
        home_lineup_pitchers = relay.get("homeLineup", {}).get("pitcher", [])
        away_lineup_pitchers = relay.get("awayLineup", {}).get("pitcher", [])

        home_pitcher = next(
            (p.get("name") for p in home_lineup_pitchers if str(p.get("pcode", "")) == current_pcode),
            None,
        )
        if home_pitcher is None and home_lineup_pitchers:
            home_pitcher = home_lineup_pitchers[-1].get("name")

        away_pitcher = next(
            (p.get("name") for p in away_lineup_pitchers if str(p.get("pcode", "")) == current_pcode),
            None,
        )
        if away_pitcher is None and away_lineup_pitchers:
            away_pitcher = away_lineup_pitchers[-1].get("name")

        # 현재 이닝 및 초/말 판단: relay 최상위 inn/homeOrAway 직접 사용
        # homeOrAway: 1=홈팀 타석(말), 0=원정팀 타석(초)
        current_inning = None
        inning_half = None  # "초" or "말"

        relay_inn = relay.get("inn")
        relay_hoa = relay.get("homeOrAway")
        if relay_inn is not None and relay_hoa is not None:
            current_inning = int(relay_inn)
            inning_half = "말" if int(relay_hoa) == 1 else "초"
        else:
            # 폴백: inningScore 기반 추론
            inning_score = relay.get("inningScore", {})
            away_innings = inning_score.get("away", {})
            home_innings = inning_score.get("home", {})
            if away_innings:
                max_inning = max(int(k) for k in away_innings.keys())
                away_val = away_innings.get(str(max_inning))
                home_val = home_innings.get(str(max_inning))
                if away_val == "-":
                    current_inning = max_inning
                    inning_half = "초"
                elif home_val == "-":
                    current_inning = max_inning
                    inning_half = "말"
                elif home_val is None:
                    current_inning = max_inning
                    inning_half = "초"
                else:
                    # 양팀 모두 완료 → 다음 이닝 초
                    current_inning = max_inning + 1
                    inning_half = "초"

        # 타선 라인업 + 오늘 성적 추출 (relay API: batter 키)
        home_hitters = relay.get("homeLineup", {}).get("batter", [])
        away_hitters = relay.get("awayLineup", {}).get("batter", [])
        home_lineup = _parse_hitter_lineup(home_hitters)
        away_lineup = _parse_hitter_lineup(away_hitters)

        logger.info(
            "[투수/라인업] game=%s | %s회%s | 홈: %s(%d명) | 원정: %s(%d명)",
            game_id,
            current_inning,
            inning_half or "",
            home_pitcher or "정보없음",
            len(home_lineup),
            away_pitcher or "정보없음",
            len(away_lineup),
        )
        return {
            "home_current_pitcher": home_pitcher,
            "away_current_pitcher": away_pitcher,
            "current_inning": current_inning,
            "inning_half": inning_half,
            "home_lineup": home_lineup,
            "away_lineup": away_lineup,
        }
    except Exception as e:
        logger.warning("Failed to fetch relay for game %s: %s", game_id, e)
        return {
            "home_current_pitcher": None,
            "away_current_pitcher": None,
            "current_inning": None,
            "inning_half": None,
            "home_lineup": [],
            "away_lineup": [],
        }


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
            # return_exceptions=True: CancelledError 등 BaseException도 결과로 처리
            raw_starters = await asyncio.gather(*starter_tasks, return_exceptions=True)
            starters_list = [
                r if isinstance(r, dict) else {"home_starter": None, "away_starter": None, "watch_points": []}
                for r in raw_starters
            ]

            # Fetch current pitchers only for live games
            live_games = [g for g in games if g.get("status") == "경기 중" and g.get("game_id")]
            if live_games:
                relay_tasks = [_fetch_current_pitchers(client, g["game_id"]) for g in live_games]
                raw_relay = await asyncio.gather(*relay_tasks, return_exceptions=True)
                relay_map = {
                    g["game_id"]: (r if isinstance(r, dict) else {"home_current_pitcher": None, "away_current_pitcher": None, "current_inning": None, "inning_half": None})
                    for g, r in zip(live_games, raw_relay)
                }
            else:
                relay_map = {}

            # Fetch result pitchers for completed games
            finished_games = [g for g in games if g.get("status") == "경기 종료" and g.get("game_id")]
            if finished_games:
                result_tasks = [_fetch_result_pitchers(client, g["game_id"]) for g in finished_games]
                raw_results = await asyncio.gather(*result_tasks, return_exceptions=True)
                result_map = {
                    g["game_id"]: (r if isinstance(r, dict) else {"winning_pitcher": None, "losing_pitcher": None, "save_pitcher": None})
                    for g, r in zip(finished_games, raw_results)
                }
            else:
                result_map = {}

        for game, starters in zip(games, starters_list):
            game["home_starter"] = starters.get("home_starter")
            game["away_starter"] = starters.get("away_starter")
            game["home_standing"] = starters.get("home_standing")
            game["away_standing"] = starters.get("away_standing")
            game["watch_points"] = starters.get("watch_points", [])
            relay = relay_map.get(game.get("game_id"), {})
            game["home_current_pitcher"] = relay.get("home_current_pitcher")
            game["away_current_pitcher"] = relay.get("away_current_pitcher")
            game["current_inning"] = relay.get("current_inning")
            game["inning_half"] = relay.get("inning_half")
            result = result_map.get(game.get("game_id"), {})
            game["winning_pitcher"] = result.get("winning_pitcher")
            game["losing_pitcher"] = result.get("losing_pitcher")
            game["save_pitcher"] = result.get("save_pitcher")
            # 라인업: 경기 중이면 relay에서, 경기 종료면 record에서, 경기 전이면 preview에서
            if relay:
                game["home_lineup"] = relay.get("home_lineup", [])
                game["away_lineup"] = relay.get("away_lineup", [])
            elif result:
                game["home_lineup"] = result.get("home_lineup", [])
                game["away_lineup"] = result.get("away_lineup", [])
            else:
                game["home_lineup"] = starters.get("home_lineup", [])
                game["away_lineup"] = starters.get("away_lineup", [])

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
        is_cancelled = game.get("cancel") is True or game.get("statusInfo") in ("경기취소", "취소")

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

        game_status = "경기 취소" if is_cancelled else _determine_game_status(status_code)

        if not stadium and home_team in TEAM_STADIUM:
            stadium = TEAM_STADIUM[home_team]

        ticket_url = ""
        for key, url in TICKET_LINKS.items():
            if key in stadium:
                ticket_url = url
                break

        # 팀명 없는 항목은 VR/방송 콘텐츠 — 야구 경기가 아니므로 제외
        if not home_team or not away_team:
            continue

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
            "home_starter": None,
            "away_starter": None,
            "home_standing": None,
            "away_standing": None,
            "home_current_pitcher": None,
            "away_current_pitcher": None,
            "current_inning": None,
            "inning_half": None,
            "winning_pitcher": None,
            "losing_pitcher": None,
            "save_pitcher": None,
            "home_lineup": [],
            "away_lineup": [],
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
        "ENDED": "경기 종료",
        "FINISH": "경기 종료",
        "CANCEL": "경기 취소",
        "POSTPONE": "경기 연기",
        "DELAY": "경기 연기",
    }
    return status_map.get(status_code, status_code or "미정")
