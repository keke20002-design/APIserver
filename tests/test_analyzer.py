from services.analyzer import _score_text, _determine_status


def test_score_negative_keywords():
    text = "잠실 주차장 만차 혼잡 상태입니다"
    score = _score_text(text)
    # 만차(+2) + 혼잡(+2) = 4
    assert score == 4


def test_score_positive_keywords():
    text = "주차장 여유 있고 널널합니다"
    score = _score_text(text)
    # 여유(-1) + 널널(-1) = -2
    assert score == -2


def test_score_mixed_keywords():
    text = "주차장 혼잡하지만 일부 자리있음"
    score = _score_text(text)
    # 혼잡(+2) + 자리있음(-1) = 1
    assert score == 1


def test_score_no_keywords():
    text = "오늘 야구 경기 재미있었다"
    score = _score_text(text)
    assert score == 0


def test_determine_status_good():
    assert _determine_status(0) == "good"
    assert _determine_status(2) == "good"


def test_determine_status_normal():
    assert _determine_status(3) == "normal"
    assert _determine_status(6) == "normal"


def test_determine_status_bad():
    assert _determine_status(7) == "bad"
    assert _determine_status(15) == "bad"
