import json as _json
import logging
import os
import re as _re
from datetime import date

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_MODEL = "gemini-2.5-flash"
FREE_DAILY_LIMIT = 5

# In-memory: {device_id: {"date": "YYYY-MM-DD", "count": int}}
_usage: dict[str, dict] = {}


def _today() -> str:
    return date.today().isoformat()


def _client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    return genai.Client(api_key=api_key)


def get_usage(device_id: str) -> dict:
    entry = _usage.get(device_id)
    today = _today()
    if not entry or entry["date"] != today:
        return {"used": 0, "limit": FREE_DAILY_LIMIT, "remaining": FREE_DAILY_LIMIT}
    used = entry["count"]
    return {"used": used, "limit": FREE_DAILY_LIMIT, "remaining": max(0, FREE_DAILY_LIMIT - used)}


def can_generate(device_id: str) -> bool:
    return get_usage(device_id)["remaining"] > 0


def increment_usage(device_id: str) -> None:
    today = _today()
    entry = _usage.get(device_id)
    if not entry or entry["date"] != today:
        _usage[device_id] = {"date": today, "count": 1}
    else:
        entry["count"] += 1


async def _generate(prompt: str, temperature: float = 0.8) -> str:
    client = _client()
    response = await client.aio.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=temperature),
    )
    return response.text


async def generate_titles(topic: str) -> str:
    prompt = f"""당신은 한국 유튜브 크리에이터 전문 카피라이터입니다.
주제: "{topic}"

이 주제로 유튜브 영상 제목 20개를 생성해주세요.

조건:
- 클릭을 유도하는 강력한 후킹 문구 포함
- 숫자, 감정 유발 단어, 트렌디한 표현 활용
- 한국어, 각 제목 15~35자 내외
- SEO 최적화 (검색 키워드 포함)
- 다양한 스타일 (질문형, 숫자형, 충격형, 공감형, 호기심형) 혼합

JSON 배열로만 응답 (다른 텍스트 없이):
["제목1", "제목2", ..., "제목20"]"""
    return await _generate(prompt, 0.9)


async def generate_description(topic: str) -> str:
    prompt = f"""당신은 한국 유튜브 SEO 전문가입니다.
주제: "{topic}"

이 주제의 유튜브 영상 설명란을 작성해주세요.

조건:
- 300~500자 분량
- 첫 2~3줄에 핵심 내용 요약 (더보기 전 표시 부분)
- 관련 키워드 자연스럽게 포함
- 시청자 행동 유도 (좋아요, 구독, 댓글)
- 타임스탬프 예시 포함 (00:00 형식)
- 관련 해시태그 5~10개

설명란 텍스트만 응답 (JSON 아닌 일반 텍스트)"""
    return await _generate(prompt, 0.7)


async def generate_tags(topic: str) -> str:
    prompt = f"""당신은 한국 유튜브 SEO 전문가입니다.
주제: "{topic}"

이 영상에 최적화된 유튜브 태그 30개를 생성해주세요.

조건:
- 핵심 키워드부터 롱테일 키워드까지 다양하게
- 한국어와 영어 혼합 (한국 유튜브 검색 최적화)
- 쉼표로 구분, 각 태그 1~4단어
- 검색량 많은 키워드 우선 배치

태그만 쉼표로 구분하여 응답 (다른 텍스트 없이)"""
    return await _generate(prompt, 0.7)


async def generate_thumbnail_text(topic: str) -> str:
    prompt = f"""당신은 유튜브 썸네일 카피라이터입니다.
주제: "{topic}"

썸네일에 넣을 짧고 강렬한 텍스트 10개를 생성해주세요.

조건:
- 각 텍스트 2~8글자 (썸네일에 크게 들어갈 텍스트)
- 시각적 임팩트 극대화
- 감정/호기심/놀라움 유발
- 한국어

JSON 배열로만 응답:
["텍스트1", "텍스트2", ..., "텍스트10"]"""
    return await _generate(prompt, 0.9)


def _extract_video_id(url: str) -> str:
    m = _re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
    if m:
        return m.group(1)
    m = _re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', url)
    if m:
        return m.group(1)
    raise ValueError(f"유효한 YouTube URL이 아닙니다: {url}")


async def _generate_with_youtube(youtube_url: str, prompt: str, temperature: float = 0.85) -> str:
    client = _client()
    response = await client.aio.models.generate_content(
        model=_MODEL,
        contents=[
            types.Part(
                file_data=types.FileData(
                    mime_type="video/mp4",
                    file_uri=youtube_url,
                )
            ),
            types.Part(text=prompt),
        ],
        config=types.GenerateContentConfig(temperature=temperature),
    )
    return response.text


async def optimize_video(topic: str, current_title: str, target: str, style: str) -> dict:
    prompt = f"""당신은 한국 유튜브 최적화 전문가입니다.

영상 정보:
- 주제: {topic}
- 현재 제목: {current_title}
- 타겟 시청자: {target}
- 영상 스타일: {style}

위 정보를 바탕으로 유튜브 업로드 최적화 결과를 아래 JSON 형식으로만 응답하세요.
순수 JSON만 응답하세요 (마크다운 코드블록, 설명 텍스트 없이):

{{
  "ctr_score": 72,
  "seo_score": 65,
  "titles": ["제목1", ..., "제목5"],
  "thumbnail_texts": ["문구1", ..., "문구5"],
  "description": "설명란 전문",
  "tags": ["태그1", ..., "태그15"],
  "pinned_comment": "고정 댓글 내용"
}}

조건:
- ctr_score: 현재 제목의 CTR 예상 점수 0~100 (숫자만)
- seo_score: 현재 제목의 SEO 점수 0~100 (숫자만)
- titles: 타겟/스타일에 맞는 클릭 유도 제목 5개, 한국어 15-35자, 숫자·감정어·트렌드 활용
- thumbnail_texts: 썸네일에 넣을 2-8글자 임팩트 문구 5개
- description: 200-300자, 첫 2줄 핵심요약, 해시태그 3-5개 포함
- tags: 한국어+영어 혼합 검색 최적화 태그 15개
- pinned_comment: 시청자 참여 유도 고정댓글 1개 (구어체, 질문형)"""

    raw = await _generate(prompt, temperature=0.8)
    match = _re.search(r'\{[\s\S]*\}', raw)
    if not match:
        raise ValueError("AI 응답에서 JSON을 파싱할 수 없습니다.")
    return _json.loads(match.group())


async def generate_script(topic: str, duration: int = 60) -> str:
    prompt = f"""당신은 한국 유튜브 쇼츠 전문 작가입니다.
주제: "{topic}"
길이: {duration}초

이 쇼츠 영상의 대본을 작성해주세요.

구성:
- 후킹 (0~3초): 시청자를 즉시 사로잡는 첫 마디
- 본론 ({duration // 3}~{duration * 2 // 3}초): 핵심 내용 전달
- 마무리 (마지막 5초): 행동 유도 (구독, 좋아요)

조건:
- 실제 말하듯 자연스러운 구어체
- [효과음], [화면전환] 등 연출 메모 포함
- {duration}초 분량 (성인 평균 말하기 속도 기준)

대본 텍스트만 응답"""
    return await _generate(prompt, 0.8)
