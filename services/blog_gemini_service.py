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

본문(content)에 <h1> 금지. 허용: <h2> <h3> <p> <table> <thead> <tbody> <tr> <th> <td> <ul> <li>

=== 길이 ===

1500~3500자 범위

=== 제목 ===

- 25자 이내 (초과 금지)
- 핵심 키워드 포함
- 사람이 실제 검색하는 형태로 작성
- 낚시성 금지
- 날짜 포함 금지

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

=== 키워드 전략 ===

뉴스 제목 그대로 사용 금지. 검색자가 실제로 입력할 검색어 형태로 변환.
예) "엔비디아 실적 발표" → "엔비디아 주가 전망" / "비트코인 급등" → "비트코인 급등 이유"

=== SEO 원칙 ===

- 핵심 정보를 첫 문단에 제공
- 불필요한 서론 제거
- 키워드 남용 금지

=== HTML 규칙 ===

content에 <h1> 금지.
허용 태그: <h2> <h3> <p> <table> <thead> <tbody> <tr> <th> <td> <ul> <li>
허용 클래스(CSS 미리 정의됨): stat-grid, stat-item, point-box, my-opinion, checklist, conclusion-box, quote-box
- 중요 인용·핵심 한 줄 강조: <div class="quote-box">문장</div>
- 핵심 문장은 <strong>으로 감쌀 것 (단, 문단당 1~2개 이내)
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


async def _generate_image_search_query(keyword: str, title: str) -> str:
    """제목 기반으로 Pexels 검색에 적합한 영어 쿼리 생성 (객체 중심, 사람/감정 없음)."""
    try:
        client = _make_client()
        response = await client.aio.models.generate_content(
            model=_MODEL,
            contents=(
                f"You are a professional photo editor. Generate a short English search query (3-5 words) "
                f"for a stock photo site to find a thumbnail image that matches this article title.\n\n"
                f"Title: {title}\nKeyword: {keyword}\n\n"
                f"Rules:\n"
                f"- Object-focused, NOT emotion or people\n"
                f"- No people, no faces, no text in image\n"
                f"- Editorial news thumbnail style\n"
                f"- Output: 3-5 English words only, no explanation"
            ),
            config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=20),
        )
        return response.text.strip().strip('"').strip("'")
    except Exception:
        return keyword


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
No <h1> in content.
Allowed tags: <h2> <h3> <p> <table> <thead> <tbody> <tr> <th> <td> <ul> <li>
Allowed classes: stat-grid, stat-item, point-box, my-opinion, checklist, conclusion-box, quote-box
Bold key phrases: <strong>phrase</strong> (max 1-2 per paragraph)
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


async def generate_post_image(keyword: str, title: str) -> tuple[bytes, str] | None:
    """Pexels API로 관련 스톡 이미지 검색 후 다운로드. 실패 시 None 반환."""
    pexels_key = os.getenv("PEXELS_API_KEY", "")
    if not pexels_key:
        logger.debug("PEXELS_API_KEY not set, skipping image")
        return None
    try:
        import httpx
        en_query = await _generate_image_search_query(keyword, title)
        logger.info("Pexels search query: '%s' (from title='%s', keyword='%s')", en_query, title, keyword)
        async with httpx.AsyncClient(timeout=15) as http:
            search_r = await http.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": pexels_key},
                params={"query": en_query, "per_page": 1, "orientation": "landscape"},
            )
            search_r.raise_for_status()
            photos = search_r.json().get("photos", [])
            if not photos:
                return None
            img_url = photos[0]["src"]["large2x"]
            img_r = await http.get(img_url)
            img_r.raise_for_status()
            return img_r.content, "image/jpeg"
    except Exception as e:
        logger.warning("Pexels image fetch failed (skipping): %s", e)
    return None
