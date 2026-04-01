from services.restaurant import _extract_popular_menus, _summarize_reviews


def test_extract_popular_menus():
    texts = [
        "치킨이 맛있고 맥주 한잔하기 좋다",
        "치킨 추천! 핫도그도 괜찮음",
        "맥주 종류가 많고 치킨이 바삭",
    ]
    menus = _extract_popular_menus(texts)
    assert len(menus) > 0
    # 치킨이 가장 많이 언급
    assert menus[0]["menu"] == "치킨"
    assert menus[0]["mentions"] >= 3


def test_extract_popular_menus_empty():
    menus = _extract_popular_menus([])
    assert menus == []


def test_extract_popular_menus_no_food_keywords():
    texts = ["오늘 경기 재밌었다", "날씨가 좋다"]
    menus = _extract_popular_menus(texts)
    assert menus == []


def test_summarize_reviews_positive():
    texts = [
        "여기 맛있어요 추천합니다 최고",
        "정말 맛있고 분위기도 좋아요",
    ]
    summary = _summarize_reviews(texts)
    assert summary["sentiment"] in ("매우 긍정적", "긍정적")
    assert summary["positive_mentions"] > summary["negative_mentions"]


def test_summarize_reviews_negative():
    texts = [
        "줄 길고 비싸고 별로",
        "실망이었고 불친절하다",
    ]
    summary = _summarize_reviews(texts)
    assert summary["negative_mentions"] > 0


def test_summarize_reviews_empty():
    summary = _summarize_reviews([])
    assert summary["sentiment"] == "정보 부족"
    assert summary["positive_mentions"] == 0
    assert summary["negative_mentions"] == 0
    assert summary["tips"] == []


def test_summarize_reviews_with_tips():
    texts = [
        "주차 가능하고 예약 필수.맛있어요",
    ]
    summary = _summarize_reviews(texts)
    assert len(summary["tips"]) > 0
