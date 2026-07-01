import json
import logging
import os
import random
import re

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_MODEL = "gemini-2.5-flash"


def _make_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    return genai.Client(api_key=api_key)


def _parse_json_safe(text: str) -> dict:
    """Gemini 응답에서 JSON 안전하게 파싱. 코드블록 제거 후 시도."""
    # 마크다운 코드블록 제거
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # content 필드 안의 개행/따옴표로 인한 파싱 오류 → content만 따로 추출
        title_m = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        meta_m = re.search(r'"meta_description"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        tags_m = re.search(r'"tags"\s*:\s*(\[.*?\])', text, re.DOTALL)
        content_m = re.search(r'"content"\s*:\s*"(.*?)"\s*\}?\s*$', text, re.DOTALL)
        if title_m and content_m:
            content_raw = content_m.group(1).replace('\\"', '"').replace("\\n", "\n")
            tags = json.loads(tags_m.group(1)) if tags_m else []
            return {
                "title": title_m.group(1),
                "meta_description": meta_m.group(1) if meta_m else "",
                "tags": tags,
                "content": content_raw,
            }
        raise

# 소제목 스타일 — 랜덤으로 선택해 프롬프트에 주입
_HEADING_STYLES = [
    "질문형: 각 H2/H3 소제목을 독자가 실제로 궁금해할 질문 형태로 작성. 예) '이번 금리 동결이 내 대출 이자에 미치는 영향은?' / '비트코인이 다시 오르는 진짜 이유는 뭔가?'",
    "핵심요약형: 각 H2/H3 소제목을 해당 섹션의 핵심 내용 + 키워드를 압축한 형태로 작성. 예) '반도체 수출 호조, 코스피 반등의 실질적 근거' / 'GPT-5 출시가 국내 AI 시장 판도를 바꾸는 이유'",
    "단정형: 각 H2/H3 소제목을 강조 포인트를 단언하는 형태로 작성. 예) '지금 체크해야 할 ETF 투자 핵심 3가지' / '이번 정책 변화로 달라지는 부동산 실수요자 전략'",
]

# 후처리: AI 특유의 상투적 소제목 강제 교체
_BAD_HEADER_MAP = {
    "왜 중요한가": "이 이슈가 주목받는 배경",
    "사용자/투자자에게 어떤 영향이 있는가": "실생활·투자에 미치는 파급효과",
    "앞으로 무엇을 봐야 하는가": "앞으로 체크해야 할 핵심 포인트",
    "핵심 요약": "지금 당장 알아야 할 핵심",
    "결론": "정리하며",
    "개요": "배경과 현황",
    "전망": "향후 방향과 시사점",
    "주요 내용": "이번 이슈의 핵심 내용",
    "영향 분석": "실질적 영향 분석",
}


def _fix_ai_headers(content: str) -> str:
    """상투적 AI 소제목 패턴을 후처리로 교체."""
    for bad, good in _BAD_HEADER_MAP.items():
        # h2, h3 태그 안에 정확히 일치하는 경우만 교체
        content = re.sub(
            rf'(<h[23][^>]*>)\s*{re.escape(bad)}\s*(</h[23]>)',
            rf'\g<1>{good}\2',
            content,
            flags=re.IGNORECASE,
        )
    return content


_PROMPT_TEMPLATE = """
당신은 한국 SEO 전문 콘텐츠 에디터다.

키워드: {keyword}
카테고리: {topic}

목표:
검색 사용자의 질문에 가장 도움이 되는 정보성 콘텐츠 작성.

=== SEO 원칙 ===

- 사용자의 검색 의도를 먼저 파악
- 결론과 핵심 정보를 초반에 제공
- 불필요한 서론 제거
- 실제로 도움이 되는 내용 우선
- 키워드 남용 금지
- 자연스러운 한국어 사용

=== 문체 ===

- 친한 전문가가 자기 노트를 독자에게 공유하듯 작성. 기자 말투·강의 말투 절대 금지.
- 절대 강의하지 않는다. 절대 설명하려 들지 않는다. 관찰한 내용을 공유하는 방식으로 쓴다.
- 글은 "전문가가 자기 노트에 기록한 내용을 독자에게 공유하는 느낌"으로 작성.
- 문장 길이를 다양하게 구성
- 동일한 문장 패턴 반복 금지
- 과도한 감탄사·과장 표현 금지
- 첫 문장은 추상적인 사회 이야기 금지. 바로 독자의 관심사로 시작.
  좋은 예: "최근 발표된 정책 중에는 생각보다 혜택이 큰 내용도 있습니다."
  나쁜 예: "우리 사회 곳곳에서 다양한 지원 정책이 이어지고 있습니다."

=== 금지 표현 (절대 사용 불가) ===

- 안녕하세요 / 전문 블로거입니다 / 여러분께
- 살펴보겠습니다 / 알아보겠습니다 / 함께 알아보겠습니다
- 소개해드립니다 / 도움이 되길 바랍니다 / 전달해 드립니다
- 지금부터 / 오늘은 ~ 알아보겠습니다
- 우리 사회 곳곳에서 / 이어지고 있습니다
- 집중하는 한편 / 아끼지 않고 있습니다 / 주목되고 있습니다
- 전달해 드리고자 합니다 / 면밀히 분석 / 한편 / 더욱

=== 권장 표현 ===

보니까 / 실제로 / 의외로 / 생각보다 / 이번에는 / 이 부분은 /
찾아보니 / 개인적으로 / 한 번쯤 / 놓치기 쉬운 / 확인해볼 만한

=== 소제목(H2/H3) 규칙 ===

"왜 중요한가" / "개요" / "결론" / "전망" / "핵심 요약" 같은 추상적 소제목 절대 금지.
반드시 해당 섹션의 핵심 키워드가 포함된 구체적 문장형 소제목으로 동적 생성.

=== 구조 ===

<p>핵심 요약 — 독자가 왜 읽어야 하는지 바로 제시</p>

H2 섹션 3~4개 (위 소제목 규칙 준수)
표(table) 1개 이상 필수, 목록(ul/li) 1개 이상 필수, 비교 정리 권장

=== FAQ (필수 3개 이상) ===

실제 독자가 검색창에 입력할 질문 3개 이상 반드시 작성.

형식:
<div class="faq-item">
<h3 class="faq-q">Q: 질문</h3>
<p class="faq-a">A: 답변</p>
</div>

=== HTML 규칙 ===

본문(content)에 <h1> 금지. <a> 태그 절대 금지 (링크 삽입 금지).
허용: <h2> <h3> <p> <table> <thead> <tbody> <tr> <th> <td> <ul> <li>

=== 길이 ===

1500~3500자 범위

=== 제목 ===

- 25자 이내 (초과 금지)
- 핵심 키워드 포함
- 날짜 포함 금지 / 낚시성 금지
- 아래 고클릭률 패턴 중 하나를 반드시 사용:
  패턴1 "~해보니" 예) Claude Code로 블로그 자동화해보니
  패턴2 "~차이" 예) ChatGPT 유료 무료 차이
  패턴3 "~방법" 예) MCP 서버 구축 방법
  패턴4 "~비용" 예) Gemini API 비용 정리
  패턴5 "~전망" 예) 엔비디아 주가 전망
  패턴6 "~이유" 예) Claude Code가 주목받는 이유

=== 메타 설명 ===

100~160자

=== 태그 ===

3~8개

=== 핵심 요약 (summary) ===

독자가 바로 알아야 할 핵심 포인트 3가지를 15~30자 문장으로 작성.
팩트 기반, 구체적 수치 포함 권장. 투자/행동 유도 문구 금지.

=== 절대 금지 ===

- 안녕하세요
- 오늘은 ~ 알아보겠습니다
- 이 글에서는
- 최고 / 완벽 / 무조건 / 100% 성공
- 허위 경험담
- 존재하지 않는 통계

반드시 JSON만 출력

{{
  "title": "",
  "meta_description": "",
  "tags": [""],
  "summary": ["핵심 포인트 1", "핵심 포인트 2", "핵심 포인트 3"],
  "content": ""
}}
"""


_ANALYTICAL_PROMPT = """
당신은 해당 분야에 정통한 한국인 전문 블로거다.
독자에게 실질적 도움이 되는 해설형 콘텐츠를 작성한다.

발행 시간대: {slot_label}
오늘 날짜: {today}

=== 수집된 뉴스 기사 ===
{news_text}
=== 뉴스 끝 ===

=== 소제목(H2/H3) 작성 규칙 [최우선] ===

{heading_style}

절대 금지 소제목:
"왜 중요한가" / "사용자/투자자에게 어떤 영향이 있는가" / "앞으로 무엇을 봐야 하는가" /
"개요" / "결론" / "전망" / "핵심 요약" / "주요 내용" / "영향 분석"

→ 이런 추상적·상투적 소제목은 AI가 썼다는 신호다. 반드시 해당 섹션의 핵심 키워드가 포함된
  구체적인 문장형 소제목으로 동적 생성할 것.

=== 뉴스 활용 원칙 [필수] ===

뉴스를 받아도 "기사 요약"을 만들지 않는다. 반드시 아래 4가지를 중심으로 재구성:
1. 왜 중요한가 — 독자에게 직접적으로 연결되는 이유
2. 실제 사용자/투자자에게 어떤 영향이 있는가
3. 활용 방법은 무엇인가 — 구체적 행동 가이드
4. 앞으로 어떻게 될 가능성이 있는가

=== 콘텐츠 흐름 (반드시 이 순서로) ===

1. <p> 첫 문단 — 독자가 왜 읽어야 하는지 바로 (추상적 사회 이야기 금지)
2. H2 섹션 (소제목 규칙 준수)
3. 핵심 수치가 있으면 stat-grid 카드로 표현:
   <div class="stat-grid">
     <div class="stat-item"><h3>수치</h3><p>설명</p></div>
     ...
   </div>
4. 중요 포인트는 point-box로:
   <div class="point-box">💡 핵심 한 문장</div>
5. 작성자 의견은 my-opinion 박스로 (뉴스와 블로그 차이):
   <div class="my-opinion"><h3>✍️ 개인적으로 본 포인트</h3><p>...</p></div>
6. 투자·행동 관련 주제면 checklist 박스 추가:
   <div class="checklist"><h3>✅ 체크포인트</h3><ul><li>...</li></ul></div>
7. 마지막에 반드시 conclusion-box로 마무리:
   <div class="conclusion-box"><h3>한눈에 정리</h3><p>...</p></div>
8. FAQ — 실제 독자가 검색할 질문 3개 이상 (필수)

=== 어조 ===

- 친한 전문가가 자기 노트를 독자에게 공유하듯 작성. 기자 말투·강의 말투 절대 금지.
- 절대 강의하지 않는다. 절대 설명하려 들지 않는다. 관찰한 내용을 공유하는 방식으로 쓴다.
- 글은 "전문가가 자기 노트에 기록한 내용을 독자에게 공유하는 느낌"으로 작성.
- ~입니다 / ~보니까 톤으로 통일
- 뉴스 리포트 말투(~했습니다, ~밝혔습니다 반복) 금지
- 첫 문장은 추상적인 사회 이야기 금지. 바로 독자의 관심사로 시작.
  좋은 예: "6월 들어 신청 가능한 지원금이 꽤 늘었습니다."
  나쁜 예: "우리 사회 곳곳에서 다양한 지원 정책이 이어지고 있습니다."
- 관찰 + 생각 + 경험을 섞어야 블로그 느낌이 남. 예시:
  "요즘 IPO 일정만 봐도 AI 기업 이름이 정말 자주 보입니다.
  불과 2~3년 전만 해도 반도체나 2차전지가 중심이었다면,
  최근에는 AI와 로봇 자동화 기업이 그 자리를 빠르게 차지하고 있는 모습입니다.
  실제로 이번 주 공모 예정 기업들을 살펴보다가 생각보다 AI 관련 기업 비중이 높아서 조금 놀랐습니다."

=== 금지 표현 (절대 사용 불가) ===

- 안녕하세요 / 전문 블로거입니다 / 여러분께
- 살펴보겠습니다 / 알아보겠습니다 / 함께 알아보겠습니다
- 소개해드립니다 / 도움이 되길 바랍니다 / 전달해 드립니다
- 지금부터 / 오늘은 ~ 알아보겠습니다
- 우리 사회 곳곳에서 / 이어지고 있습니다
- 집중하는 한편 / 아끼지 않고 있습니다 / 주목되고 있습니다
- 전달해 드리고자 합니다 / 면밀히 분석 / 한편 / 더욱

=== 권장 표현 ===

보니까 / 실제로 / 의외로 / 생각보다 / 이번에는 / 이 부분은 /
찾아보니 / 개인적으로 / 한 번쯤 / 놓치기 쉬운 / 확인해볼 만한

=== [최우선] 추론·창작 금지 ===

뉴스 기사에 없는 수치·발언·일정·결과 창작 절대 금지.
불확실한 내용 → "~로 알려졌습니다", "~로 전해집니다" 표현 사용.

=== E-E-A-T ===

- 정확성 우선, 사실 기반
- 출처 있는 내용만 단정적 표현
- 최신 정보 반영 (기준: {today})

=== 뉴스 활용 원칙 [최우선] ===

뉴스를 받아도 "기사 요약"을 만들지 않는다. 반드시 아래 4가지를 중심으로 재구성:
1. 왜 중요한가 — 독자에게 직접적으로 연결되는 이유
2. 실제 사용자/투자자에게 어떤 영향이 있는가
3. 활용 방법은 무엇인가 — 구체적 행동 가이드
4. 앞으로 어떻게 될 가능성이 있는가

절대 금지 제목 패턴: "오늘의 ~뉴스", "AI 뉴스 모음", "~뉴스 브리핑", "일일 요약"

=== 제목 패턴 [고클릭률 — 반드시 하나 선택] ===

패턴1 "~해보니" 예) Claude Code로 블로그 자동화해보니
패턴2 "~차이" 예) ChatGPT 유료 무료 차이
패턴3 "~방법" 예) MCP 서버 구축 방법
패턴4 "~비용" 예) Gemini API 비용 정리
패턴5 "~전망" 예) 엔비디아 주가 전망
패턴6 "~이유" 예) Claude Code가 주목받는 이유

=== 키워드 전략 ===

뉴스 제목 그대로 사용 금지. 검색자가 실제로 입력할 검색어 형태로 변환.
예) "엔비디아 실적 발표" → "엔비디아 주가 전망" / "비트코인 급등" → "비트코인 급등 이유"

=== SEO 원칙 ===

- 핵심 정보를 첫 문단에 제공
- 불필요한 서론 제거
- 키워드 남용 금지

=== HTML 규칙 ===

content에 <h1> 금지. <a> 태그 절대 금지 (URL·링크 삽입 금지).
허용 태그: <h2> <h3> <p> <table> <thead> <tbody> <tr> <th> <td> <ul> <li> <mark>
허용 클래스(CSS 미리 정의됨): stat-grid, stat-item, point-box, my-opinion, checklist, conclusion-box, quote-box
- 중요 인용·핵심 한 줄 강조: <div class="quote-box">문장</div>
- 독자가 반드시 기억해야 할 핵심 문구(2~5단어)는 <mark>문구</mark>로 감싸라. 문단당 최대 2개. <strong> 대신 <mark> 사용.
FAQ: <div class="faq-item"><h3 class="faq-q">Q: 질문</h3><p class="faq-a">A: 답변</p></div>

=== 길이 ===

1500~3000자

=== 핵심 요약 (summary) ===

독자가 바로 알아야 할 핵심 포인트 3가지를 15~30자 문장으로 작성.
팩트 기반, 구체적 수치 포함 권장. 투자/행동 유도 문구 금지.

=== 절대 금지 ===

- 안녕하세요 / 이 글에서는 / 오늘은 ~ 알아보겠습니다
- 최고 / 완벽 / 무조건
- 뉴스에 없는 내용 창작
- content 안에 <h1>

반드시 JSON만 출력

{{
  "keyword": "검색어 형태로 변환된 SEO 키워드 (뉴스 제목 아님)",
  "title": "25자 이내, 사람이 실제 검색하는 형태, 핵심 키워드 포함, 낚시성·날짜 금지",
  "meta_description": "100~160자",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"],
  "summary": ["핵심 포인트 1 (15~30자)", "핵심 포인트 2", "핵심 포인트 3"],
  "content": "WordPress HTML 본문"
}}
"""

_SLOT_LABELS = {
    "morning": "오전 (AI·기술 분석)",
    "afternoon": "오후 (AI·투자 분석)",
    "evening": "저녁 (AI·시장 분석)",
}


async def generate_analytical_post(news_items: list[dict], slot: str) -> dict:
    """
    뉴스 기사를 해설형 블로그로 생성.
    keyword, title, meta_description, tags, content + keyword 반환.
    """
    from datetime import date
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    if not news_items:
        raise RuntimeError("뉴스 데이터가 없습니다")

    today = date.today().strftime("%Y년 %m월 %d일")
    slot_label = _SLOT_LABELS.get(slot, slot)

    news_text = ""
    for i, item in enumerate(news_items[:10], 1):
        news_text += f"[기사 {i}] {item['title']}\n"
        if item.get("description"):
            news_text += f"내용: {item['description']}\n"
        news_text += "\n"

    client = genai.Client(api_key=api_key)
    heading_style = random.choice(_HEADING_STYLES)
    prompt = _ANALYTICAL_PROMPT.format(
        slot_label=slot_label, today=today, news_text=news_text,
        heading_style=heading_style,
    )
    response = await client.aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.75,
            max_output_tokens=8192,
        ),
    )

    try:
        data = _parse_json_safe(response.text)
    except json.JSONDecodeError as e:
        logger.error("Gemini JSON parse failed: %s\nRaw: %s", e, response.text[:500])
        raise RuntimeError(f"Gemini 응답 파싱 실패: {e}")

    required = {"keyword", "title", "meta_description", "tags", "content"}
    missing = required - data.keys()
    if missing:
        raise RuntimeError(f"Gemini 응답에 필드 누락: {missing}")

    data.setdefault("summary", [])

    # 후처리: 상투적 소제목 교체
    data["content"] = _fix_ai_headers(data["content"])

    logger.info(
        "Analytical post generated — slot=%s, style=%s, keyword='%s', title='%s', chars=%d",
        slot, heading_style[:10], data["keyword"], data["title"], len(data["content"]),
    )
    return data


# 이전 버전 호환 유지 (pipeline fallback에서 사용)
async def generate_blog_post_from_news(keyword: str, topic: str, news_items: list[dict]) -> dict:
    return await generate_analytical_post(news_items, slot="evening")


async def generate_blog_post(keyword: str, topic: str) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    client = genai.Client(api_key=api_key)
    prompt = _PROMPT_TEMPLATE.format(keyword=keyword, topic=topic)
    response = await client.aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.85,
            max_output_tokens=8192,
        ),
    )

    try:
        data = _parse_json_safe(response.text)
    except json.JSONDecodeError as e:
        logger.error("Gemini JSON parse failed: %s\nRaw: %s", e, response.text[:500])
        raise RuntimeError(f"Gemini 응답 파싱 실패: {e}")

    required = {"title", "meta_description", "tags", "content"}
    missing = required - data.keys()
    if missing:
        raise RuntimeError(f"Gemini 응답에 필드 누락: {missing}")

    data.setdefault("summary", [])
    data["content"] = _fix_ai_headers(data["content"])
    logger.info(
        "Blog post generated — keyword='%s', title='%s', chars=%d",
        keyword, data["title"], len(data["content"]),
    )
    return data


_CATEGORY_PEXELS_FALLBACKS: dict[str, list[str]] = {
    "AI":            ["artificial intelligence chip", "machine learning server", "neural network abstract", "AI technology future", "data center servers"],
    "Automation":    ["workflow automation software", "robotic process code", "server rack cloud", "digital automation pipeline", "automated robot factory"],
    "Market":        ["stock market chart", "financial trading screen", "bull market graph", "investment portfolio", "wall street finance"],
    "Markets":       ["stock market chart", "financial trading screen", "bull market graph", "investment portfolio", "wall street finance"],
    "Crypto":        ["bitcoin cryptocurrency", "blockchain network", "crypto trading terminal", "digital currency gold", "ethereum digital coin"],
    "Guides":        ["tutorial guide laptop", "learning education desk", "productivity workspace", "open book knowledge", "step instructions"],
    "Economy":       ["economy business growth", "financial report chart", "gdp growth graph", "economic analysis", "business finance"],
    "Side Hustle":   ["freelancer laptop coffee", "remote work desk setup", "online business money", "side hustle income", "entrepreneur startup"],
    "Consumer Tech": ["smartphone technology", "laptop computer modern", "tech gadget product", "apple device minimal", "consumer electronics"],
    "Trending":      ["trending news technology", "viral social media", "breaking news screen", "digital news feed", "modern media"],
}


async def _generate_image_search_query(keyword: str, title: str, category: str = "AI") -> str:
    """제목 기반으로 Pexels 영어 쿼리 생성. 실패하거나 한국어 포함 시 카테고리별 폴백 사용."""
    try:
        client = _make_client()
        response = await client.aio.models.generate_content(
            model=_MODEL,
            contents=(
                f"Generate a short English search query (3-5 words) for Pexels stock photos "
                f"matching this article. Object/concept only — no people, no faces, no text.\n\n"
                f"Title: {title}\n\n"
                f"Output: 3-5 English words only, nothing else."
            ),
            config=types.GenerateContentConfig(temperature=0.5, max_output_tokens=20),
        )
        query = response.text.strip().strip('"').strip("'")
        if any(ord(c) > 127 for c in query):
            raise ValueError("non-ASCII in query")
        return query
    except Exception as e:
        fallbacks = _CATEGORY_PEXELS_FALLBACKS.get(category, _CATEGORY_PEXELS_FALLBACKS["AI"])
        fallback = random.choice(fallbacks)
        logger.debug("Image query fallback (reason=%s) → '%s'", e, fallback)
        return fallback


_KR_GUIDE_KEYWORDS: list[dict] = [
    # AI 도구 (70%) — 최우선
    {"keyword": "Claude Code 자동화 방법",          "category": "Automation"},
    {"keyword": "ChatGPT 유료 무료 차이",            "category": "AI"},
    {"keyword": "Gemini API 비용 정리",              "category": "AI"},
    {"keyword": "MCP 서버 구축 방법",               "category": "Automation"},
    {"keyword": "Claude Code vs Cursor 차이",        "category": "AI"},
    {"keyword": "AI Agent 만드는 방법",              "category": "Automation"},
    {"keyword": "OpenAI API 사용 방법",              "category": "Guides"},
    {"keyword": "Cursor AI 사용해보니",              "category": "AI"},
    {"keyword": "Gemini 2.5 Flash 써보니",           "category": "AI"},
    {"keyword": "Claude Pro 결제할 가치가 있을까",   "category": "AI"},
    {"keyword": "AI 자동화 사례 정리",               "category": "Automation"},
    {"keyword": "ChatGPT 최신 기능 정리",            "category": "AI"},
    {"keyword": "Claude Code로 블로그 자동화해보니", "category": "Automation"},
    {"keyword": "MCP 활용법 정리",                   "category": "Automation"},
    {"keyword": "Gemini API vs OpenAI API 비교",     "category": "AI"},
    {"keyword": "AI 코딩 어시스턴트 비교",           "category": "Guides"},
    {"keyword": "Claude Code 설치 방법",             "category": "Guides"},
    # 투자 (30%) — 다음 우선순위
    {"keyword": "엔비디아 주가 전망",                "category": "Market"},
    {"keyword": "비트코인 ETF 영향",                 "category": "Crypto"},
    {"keyword": "미국주식 AI 관련주 전망",           "category": "Market"},
    {"keyword": "ETF 투자 방법 정리",                "category": "Market"},
    {"keyword": "AI 수혜주 찾는 방법",               "category": "Market"},
    {"keyword": "반도체 주가 전망",                  "category": "Market"},
]

_KR_GUIDE_PROMPT = """
당신은 "AI + 자동화 + 투자" 전문 블로거다.
검색 유입과 장기 트래픽 확보가 목표다. 뉴스 요약이 아닌, 직접 사용 경험과 분석 중심으로 작성한다.

주제 키워드: {keyword}
카테고리: {category}
오늘 날짜: {today}

=== 제목 패턴 [반드시 하나 선택] ===

패턴1 "~해보니"   예) Claude Code로 블로그 자동화해보니
패턴2 "~차이"     예) ChatGPT 유료 무료 차이
패턴3 "~방법"     예) MCP 서버 구축 방법
패턴4 "~비용"     예) Gemini API 비용 정리
패턴5 "~전망"     예) 엔비디아 주가 전망
패턴6 "~이유"     예) Claude Code가 주목받는 이유

제목 규칙: 25자 이내 / 날짜 금지 / 낚시성 금지 / 핵심 키워드 포함

=== 콘텐츠 구성 원칙 ===

반드시 아래 4가지를 중심으로 작성:
1. 왜 중요한가 — 독자에게 직접 연결되는 이유
2. 실제 사용자/투자자에게 어떤 영향이 있는가
3. 활용 방법은 무엇인가 — 구체적 행동 가이드
4. 앞으로 어떻게 될 가능성이 있는가

=== 소제목(H2/H3) 규칙 ===

{heading_style}

절대 금지: "왜 중요한가" / "개요" / "결론" / "전망" / "핵심 요약" 같은 추상적 소제목.
해당 섹션의 핵심 키워드가 담긴 구체적 문장형으로 작성.

=== 어조 ===

- 직접 써본 블로거처럼: "직접 써보니", "실제로 적용해보면", "생각보다", "의외로", "가장 놀랐던 점은"
- 강의·설명 금지. 관찰과 경험 공유 방식으로.
- 뉴스 기자 말투 금지 (~했습니다, ~밝혔습니다 반복).

=== 금지 표현 ===

살펴보겠습니다 / 알아보겠습니다 / 소개해드립니다 / 전달해드립니다 /
안녕하세요 / 오늘은 ~ 알아보겠습니다 / 도움이 되길 바랍니다

=== 콘텐츠 흐름 ===

1. <p> 첫 문단 — 독자 관심사 직접 시작, 추상적 사회 이야기 금지
2. H2 섹션 3~4개
3. 핵심 수치 → stat-grid
4. 핵심 포인트 → point-box
5. 개인 의견/경험 → my-opinion
6. 행동 가이드 → checklist (투자·사용 관련이면 필수)
7. 마무리 → conclusion-box
8. FAQ 3개 이상 (실제 검색어 형태)

=== HTML 규칙 ===

content에 <h1> 금지.
허용 태그: <h2> <h3> <p> <table> <thead> <tbody> <tr> <th> <td> <ul> <li> <mark>
허용 클래스: stat-grid, stat-item, point-box, my-opinion, checklist, conclusion-box, quote-box
독자가 반드시 기억해야 할 핵심 문구(2~5단어)는 <mark>문구</mark>로 감싸라. 문단당 최대 2개.
FAQ: <div class="faq-item"><h3 class="faq-q">Q: 질문</h3><p class="faq-a">A: 답변</p></div>

=== 길이 === 1500~3000자

=== 핵심 요약 ===
독자가 바로 알아야 할 핵심 포인트 3가지를 15~30자 문장으로. 팩트 기반.

반드시 JSON만 출력:
{{
  "keyword": "{keyword}",
  "title": "25자 이내, 위 패턴 중 하나, 핵심 키워드 포함",
  "meta_description": "100~160자",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"],
  "summary": ["핵심 포인트 1 (15~30자)", "핵심 포인트 2", "핵심 포인트 3"],
  "content": "WordPress HTML 본문"
}}
"""


async def generate_kr_guide_post(
    used_keywords: list[str] | None = None,
    exclude_categories: set[str] | None = None,
    *,
    forced_keyword: str | None = None,
    forced_category: str | None = None,
) -> dict:
    """한국어 검색형 가이드 포스트 생성.
    forced_keyword/forced_category가 있으면 topic engine 결과를 우선 사용.
    없으면 기존 고정 풀에서 폴백 선택.
    """
    from datetime import date
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    today = date.today().strftime("%Y년 %m월 %d일")
    heading_style = random.choice(_HEADING_STYLES)

    if forced_keyword and forced_category:
        keyword = forced_keyword
        category = forced_category
    else:
        pool = _KR_GUIDE_KEYWORDS
        if used_keywords:
            pool = [k for k in pool if k["keyword"] not in used_keywords] or pool
        if exclude_categories:
            filtered = [k for k in pool if k["category"] not in exclude_categories]
            pool = filtered or pool
        chosen = random.choice(pool)
        keyword = chosen["keyword"]
        category = chosen["category"]

    client = genai.Client(api_key=api_key)
    prompt = _KR_GUIDE_PROMPT.format(
        keyword=keyword, category=category, today=today, heading_style=heading_style,
    )
    response = await client.aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.85,
            max_output_tokens=8192,
        ),
    )

    try:
        data = _parse_json_safe(response.text)
    except json.JSONDecodeError as e:
        logger.error("KR guide JSON parse failed: %s\nRaw: %s", e, response.text[:500])
        raise RuntimeError(f"KR guide 파싱 실패: {e}")

    required = {"keyword", "title", "meta_description", "tags", "content"}
    missing = required - data.keys()
    if missing:
        raise RuntimeError(f"KR guide 필드 누락: {missing}")

    data.setdefault("summary", [])
    data["_category"] = category
    data["content"] = _fix_ai_headers(data["content"])
    logger.info(
        "KR guide post generated — keyword='%s', title='%s', chars=%d",
        keyword, data["title"], len(data["content"]),
    )
    return data


_EN_SLOT_LABELS = {
    "morning": "Morning (AI & Tech)",
    "afternoon": "Afternoon (AI & Markets)",
    "evening": "Evening (AI & Market Analysis)",
}

_EN_HEADING_STYLES = [
    "Question-form: Each H2/H3 as a real question readers would Google. E.g. 'Is Claude Code Better Than Cursor?' / 'How Does This Affect My Portfolio?'",
    "Summary-form: Each H2/H3 as a keyword-rich section summary. E.g. 'Nvidia Q3 Earnings: What the Numbers Actually Mean' / 'Why Claude Code Is Replacing Traditional Dev Workflows'",
    "Statement-form: Each H2/H3 as a bold claim or key insight. E.g. '3 Things to Know Before Buying AI Stocks' / 'The Real Reason Bitcoin Is Rallying Again'",
]

_ENGLISH_ANALYTICAL_PROMPT = """
You are an expert content writer for an English-language blog targeting global readers interested in AI tools, automation, and investing.

Category: {category}
Publish time: {slot_label}
Date: {today}

=== NEWS CONTEXT ===
{news_text}
=== END NEWS ===

Write an ORIGINAL English blog post — not a translation. Think about what English speakers actually search for on this topic.

=== HEADING STYLE ===
{heading_style}

Forbidden headings: "Overview", "Introduction", "Conclusion", "Key Takeaways", "What Is...", "Why It Matters", "Final Thoughts", "Summary"
→ Use specific, keyword-rich headings that reflect the actual section content.

=== CONTENT FLOW ===
1. <p> Opening — hook immediately, no abstract intros
2. H2 sections (3-4), following heading style above
3. Key stats → stat-grid:
   <div class="stat-grid"><div class="stat-item"><h3>Number</h3><p>Label</p></div></div>
4. Core insight → point-box:
   <div class="point-box">💡 One key sentence</div>
5. Personal take → my-opinion:
   <div class="my-opinion"><h3>✍️ My Take</h3><p>...</p></div>
6. Action items (if relevant) → checklist:
   <div class="checklist"><h3>✅ What to Watch</h3><ul><li>...</li></ul></div>
7. Wrap up → conclusion-box:
   <div class="conclusion-box"><h3>Bottom Line</h3><p>...</p></div>
8. FAQ — 3+ real questions English speakers Google (required)

=== TONE ===
- Like a knowledgeable friend sharing notes — NOT a journalist or professor
- First-person observations: "I've been watching...", "What struck me...", "Turns out..."
- Vary sentence length. Short punchy sentences work.
- NO: "In today's fast-paced world", "Let's dive in", "As we can see", "It goes without saying"
- YES: "Turns out...", "Worth noting...", "Here's the thing...", "What I found..."

=== FORBIDDEN PHRASES ===
- "In today's rapidly evolving landscape"
- "Let me walk you through"
- "In conclusion" / "To summarize"
- "It is important to note"
- "As mentioned above"

=== FACTUAL ACCURACY ===
Only state facts from the news context. For uncertain info: "reportedly", "according to reports".
Do NOT invent statistics or quotes not present in the news.

=== HTML RULES ===
No <h1> in content. No <a> tags — absolutely no links or URLs in the content.
Allowed tags: <h2> <h3> <p> <table> <thead> <tbody> <tr> <th> <td> <ul> <li> <mark>
Allowed classes: stat-grid, stat-item, point-box, my-opinion, checklist, conclusion-box, quote-box
Highlight key phrases (2-5 words) readers MUST remember: <mark>phrase</mark> (max 2 per paragraph). Use <mark> instead of <strong>.
FAQ: <div class="faq-item"><h3 class="faq-q">Q: question</h3><p class="faq-a">A: answer</p></div>

=== LENGTH ===
1500-3000 characters

=== SUMMARY ===
3 bullet points readers need immediately (15-30 words each). Fact-based, include specific numbers where available.

Output JSON only:
{{
  "keyword": "exact phrase English speakers Google (e.g. 'Claude Code automation guide')",
  "title": "Under 60 chars — real search form, includes keyword, no clickbait, no dates",
  "meta_description": "150-160 characters",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "summary": ["Key point 1 (15-30 words)", "Key point 2", "Key point 3"],
  "content": "WordPress HTML body"
}}
"""


async def generate_english_post(
    news_items: list[dict],
    slot: str,
    category: str,
    keyword: str = "",
) -> dict:
    """뉴스 기반 영어 포스트 생성 (한국어 포스트와 asyncio.gather로 동시 생성)."""
    from datetime import date
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    today = date.today().strftime("%B %d, %Y")
    slot_label = _EN_SLOT_LABELS.get(slot, slot)
    heading_style = random.choice(_EN_HEADING_STYLES)

    if news_items:
        news_text = ""
        for i, item in enumerate(news_items[:10], 1):
            news_text += f"[Article {i}] {item.get('title', '')}\n"
            if item.get("description"):
                news_text += f"Summary: {item['description']}\n"
            news_text += "\n"
    else:
        topic = keyword or category
        news_text = f"Topic: {topic}\n(No specific news — write a practical evergreen guide based on your knowledge.)"

    client = genai.Client(api_key=api_key)
    prompt = _ENGLISH_ANALYTICAL_PROMPT.format(
        category=category,
        slot_label=slot_label,
        today=today,
        news_text=news_text,
        heading_style=heading_style,
    )

    response = await client.aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.8,
            max_output_tokens=8192,
        ),
    )

    try:
        data = _parse_json_safe(response.text)
    except json.JSONDecodeError as e:
        logger.error("English post JSON parse failed: %s\nRaw: %s", e, response.text[:500])
        raise RuntimeError(f"English post 파싱 실패: {e}")

    required = {"keyword", "title", "meta_description", "tags", "content"}
    missing = required - data.keys()
    if missing:
        raise RuntimeError(f"English post 필드 누락: {missing}")

    data.setdefault("summary", [])
    logger.info(
        "English post generated — slot=%s, style=%s, keyword='%s', title='%s', chars=%d",
        slot, heading_style[:15], data["keyword"], data["title"], len(data["content"]),
    )
    return data


_EN_GUIDE_KEYWORDS: list[dict] = [
    {"keyword": "Claude Code automation guide",       "category": "Automation"},
    {"keyword": "best MCP servers for developers",    "category": "Guides"},
    {"keyword": "Claude Code vs Cursor AI",           "category": "AI"},
    {"keyword": "how to use Gemini API free",         "category": "Guides"},
    {"keyword": "AI workflow automation tools 2025",  "category": "Automation"},
    {"keyword": "Cursor AI review for beginners",     "category": "AI"},
    {"keyword": "how to build AI agent with Python",  "category": "Automation"},
    {"keyword": "best AI coding assistants ranked",   "category": "AI"},
    {"keyword": "Claude API vs OpenAI API comparison","category": "AI"},
    {"keyword": "MCP server setup tutorial",          "category": "Guides"},
    {"keyword": "AI automation examples for work",    "category": "Automation"},
    {"keyword": "how to use Claude Code effectively", "category": "Guides"},
    {"keyword": "Gemini 2.5 Flash vs GPT-4o speed",  "category": "AI"},
    {"keyword": "n8n AI workflow tutorial",           "category": "Automation"},
    {"keyword": "prompt engineering best practices",  "category": "Guides"},
    {"keyword": "Claude vs ChatGPT for coding",       "category": "AI"},
    {"keyword": "how to automate blog posts with AI", "category": "Automation"},
    {"keyword": "AI tools for content creators 2025", "category": "Guides"},
    {"keyword": "local LLM setup guide Ollama",       "category": "Guides"},
    {"keyword": "AI investment tools and screeners",  "category": "Markets"},
]

_EN_GUIDE_PROMPT = """
You are an expert English-language blogger writing practical How-To content for developers, investors, and tech enthusiasts.

Topic keyword: {keyword}
Category: {category}
Date: {today}

Write a PRACTICAL, ACTIONABLE English guide — not a news article. Readers are searching for step-by-step advice, comparisons, or real-world examples.

=== HEADING STYLE ===
{heading_style}

Forbidden headings: "Introduction", "Overview", "Conclusion", "Summary", "What Is...", "Final Thoughts"
→ Use specific, concrete headings. Include the keyword or a related term.

=== CONTENT FLOW ===
1. <p> Hook — state the problem/benefit immediately. No "In today's world..." intros.
2. H2 sections (3-4): practical steps, comparisons, real examples
3. Key stats or specs → stat-grid (if applicable):
   <div class="stat-grid"><div class="stat-item"><h3>Value</h3><p>Label</p></div></div>
4. Core tip → point-box:
   <div class="point-box">💡 One actionable insight</div>
5. Personal take or common mistake → my-opinion:
   <div class="my-opinion"><h3>✍️ My Take</h3><p>...</p></div>
6. Checklist or quick-start steps (required):
   <div class="checklist"><h3>✅ Quick Checklist</h3><ul><li>...</li></ul></div>
7. Wrap-up → conclusion-box:
   <div class="conclusion-box"><h3>Bottom Line</h3><p>...</p></div>
8. FAQ — 3+ questions English speakers actually Google about this topic (required)

=== TONE ===
- Practical, direct, like a senior dev sharing notes
- Use "you", active voice, short sentences
- First-person observations welcome: "What I found...", "Here's the thing...", "Turns out..."
- NO: "In today's rapidly evolving landscape", "Let's dive in", "It's worth noting that", "As we can see"

=== HTML RULES ===
No <h1> in content. No <a> tags — absolutely no links or URLs in the content.
Allowed tags: <h2> <h3> <p> <table> <thead> <tbody> <tr> <th> <td> <ul> <li> <mark>
Allowed classes: stat-grid, stat-item, point-box, my-opinion, checklist, conclusion-box, quote-box
Highlight key phrases (2-5 words) readers MUST remember: <mark>phrase</mark> (max 2 per paragraph). Use <mark> instead of <strong>.
FAQ: <div class="faq-item"><h3 class="faq-q">Q: question</h3><p class="faq-a">A: answer</p></div>

=== LENGTH ===
1500-3000 characters

=== SUMMARY ===
3 bullet points — key takeaways a reader should remember (15-30 words each). Actionable.

Output JSON only:
{{
  "keyword": "{keyword}",
  "title": "Under 60 chars — exact search form, includes keyword, no clickbait, no dates",
  "meta_description": "150-160 characters",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "summary": ["Takeaway 1 (15-30 words)", "Takeaway 2", "Takeaway 3"],
  "content": "WordPress HTML body"
}}
"""


async def generate_english_guide_post(
    used_keywords: list[str] | None = None,
    *,
    forced_keyword: str | None = None,
    forced_category: str | None = None,
) -> dict:
    """영어 How-To 가이드 포스트 생성.
    forced_keyword/forced_category가 있으면 topic engine 결과를 우선 사용.
    없으면 기존 고정 풀에서 폴백 선택.
    """
    from datetime import date
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")

    today = date.today().strftime("%B %d, %Y")
    heading_style = random.choice(_EN_HEADING_STYLES)

    if forced_keyword and forced_category:
        keyword = forced_keyword
        category = forced_category
    else:
        pool = _EN_GUIDE_KEYWORDS
        if used_keywords:
            pool = [k for k in pool if k["keyword"] not in used_keywords] or _EN_GUIDE_KEYWORDS
        chosen = random.choice(pool)
        keyword = chosen["keyword"]
        category = chosen["category"]

    client = genai.Client(api_key=api_key)
    prompt = _EN_GUIDE_PROMPT.format(
        keyword=keyword, category=category, today=today, heading_style=heading_style,
    )
    response = await client.aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.85,
            max_output_tokens=8192,
        ),
    )

    try:
        data = _parse_json_safe(response.text)
    except json.JSONDecodeError as e:
        logger.error("EN guide JSON parse failed: %s\nRaw: %s", e, response.text[:500])
        raise RuntimeError(f"EN guide 파싱 실패: {e}")

    required = {"keyword", "title", "meta_description", "tags", "content"}
    missing = required - data.keys()
    if missing:
        raise RuntimeError(f"EN guide 필드 누락: {missing}")

    data.setdefault("summary", [])
    data["_category"] = category
    logger.info(
        "EN guide post generated — keyword='%s', title='%s', chars=%d",
        keyword, data["title"], len(data["content"]),
    )
    return data


async def generate_post_image(keyword: str, title: str, category: str = "AI") -> tuple[bytes, str] | None:
    """Pexels API로 관련 스톡 이미지 검색 후 다운로드. 실패 시 None 반환."""
    pexels_key = os.getenv("PEXELS_API_KEY", "")
    if not pexels_key:
        logger.debug("PEXELS_API_KEY not set, skipping image")
        return None
    try:
        import httpx
        en_query = await _generate_image_search_query(keyword, title, category)
        logger.info("Pexels search query: '%s' (category=%s)", en_query, category)
        async with httpx.AsyncClient(timeout=15) as http:
            search_r = await http.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": pexels_key},
                params={"query": en_query, "per_page": 15, "orientation": "landscape"},
            )
            search_r.raise_for_status()
            photos = search_r.json().get("photos", [])
            if not photos:
                return None
            photo = random.choice(photos)
            img_url = photo["src"]["large2x"]
            img_r = await http.get(img_url)
            img_r.raise_for_status()
            logger.info("Pexels photo selected: id=%s", photo["id"])
            return img_r.content, "image/jpeg"
    except Exception as e:
        logger.warning("Pexels image fetch failed (skipping): %s", e)
    return None
