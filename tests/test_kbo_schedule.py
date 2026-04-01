from services.kbo_schedule import _parse_schedule, _determine_game_status


def test_parse_schedule_basic():
    data = {
        "result": {
            "games": [
                {
                    "homeTeamName": "LG",
                    "awayTeamName": "두산",
                    "gameDateTime": "2026-04-01T18:30:00",
                    "statusCode": "BEFORE",
                    "homeTeamScore": "",
                    "awayTeamScore": "",
                    "stadiumName": "잠실종합운동장",
                },
            ]
        }
    }
    games = _parse_schedule(data, "2026-04-01")
    assert len(games) == 1
    g = games[0]
    assert g["home_team"] == "LG"
    assert g["away_team"] == "두산"
    assert g["time"] == "18:30"
    assert g["status"] == "경기 전"
    assert g["ticket_url"] == "https://www.ticketlink.co.kr/sports/baseball/59"
    assert g["date"] == "2026-04-01"


def test_parse_schedule_with_score():
    data = {
        "result": {
            "games": [
                {
                    "homeTeamName": "KIA",
                    "awayTeamName": "삼성",
                    "gameDateTime": "2026-04-01T14:00:00",
                    "statusCode": "RESULT",
                    "homeTeamScore": "5",
                    "awayTeamScore": "3",
                    "stadiumName": "광주기아챔피언스필드",
                },
            ]
        }
    }
    games = _parse_schedule(data, "2026-04-01")
    assert games[0]["score"] == "3:5"
    assert games[0]["status"] == "경기 종료"


def test_parse_schedule_empty():
    data = {"result": {"games": []}}
    games = _parse_schedule(data, "2026-04-01")
    assert games == []


def test_parse_schedule_fallback_shape():
    data = {
        "games": [
            {
                "homeTeamName": "키움",
                "awayTeamName": "SSG",
                "gameDateTime": "2026-04-01T18:30:00",
                "statusCode": "STARTED",
                "homeTeamScore": "2",
                "awayTeamScore": "1",
                "stadiumName": "고척스카이돔",
            },
        ]
    }
    games = _parse_schedule(data, "2026-04-01")
    assert len(games) == 1
    assert games[0]["status"] == "경기 중"


def test_determine_game_status():
    assert _determine_game_status("BEFORE") == "경기 전"
    assert _determine_game_status("STARTED") == "경기 중"
    assert _determine_game_status("RESULT") == "경기 종료"
    assert _determine_game_status("CANCEL") == "경기 취소"
    assert _determine_game_status("POSTPONE") == "경기 연기"
    assert _determine_game_status("") == "미정"
    assert _determine_game_status("UNKNOWN") == "UNKNOWN"
