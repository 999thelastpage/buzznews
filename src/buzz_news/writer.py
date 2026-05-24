import json
import logging
from dataclasses import dataclass

from sqlalchemy import select

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.models import RawItem, Source

settings = get_settings()
log = logging.getLogger("buzz_news.writer")


@dataclass
class ArticleDraft:
    title_en: str
    body_en: str
    title_hi: str | None
    body_hi: str | None


def _build_sources_block(items: list[dict], sources: list[dict]) -> str:
    blocks = []
    for item, src in zip(items, sources):
        authority = src.get("authority", 0.5)
        published = item.get("published_at")
        iso = published.isoformat() if published else "unknown"
        body = (item.get("body") or item.get("snippet") or "")[:800]
        blocks.append(
            f"[Source: {src['name']} | Authority: {authority} | Published: {iso}]\n"
            f"Title: {item['title']}\n"
            f"Body: {body}\n"
            f"URL: {item['url']}\n---"
        )
    return "\n".join(blocks)


def _call_gemini(prompt: str, temperature: float = 0.3, max_tokens: int = 900) -> dict:
    from google import genai
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL_TEXT,
        contents=prompt,
        config={
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
            "response_schema": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}}, "required": ["title", "body"]},
        },
    )
    raw = response.text.strip()
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


def _call_anthropic(prompt: str, temperature: float = 0.3, max_tokens: int = 900) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


EN_WRITER_PROMPT = """You are an editorial summarizer for a news aggregation site. Your job is to synthesize a short editorial summary from multiple sources covering the same event, in English.

STRICT RULES:
- Output strictly valid JSON: {{"title": string, "body": string}}
- The body must be 150–250 words
- Synthesize across sources; do not copy any source verbatim
- No quoted phrases longer than 8 words
- Attribute claims inline: "Reuters reports...", "according to BBC..."
- Use a neutral journalistic tone
- Do not invent facts. If sources disagree, note the disagreement
- Do not include opinions, predictions, or editorial commentary
- If sources mention a clear next step or upcoming event, you may end with a single "What's next:" sentence
- Title: 6–12 words, sentence case, no clickbait

SOURCES:
{sources_block}

Output JSON only. No prose before or after."""


HI_WRITER_PROMPT = """You are an editorial summarizer for a news aggregation site. Your job is to synthesize a short editorial summary from multiple sources covering the same event, in Hindi (हिन्दी).

STRICT RULES:
- Output strictly valid JSON: {{"title": string, "body": string}}
- The body must be 150–250 words
- Synthesize across sources; do not copy any source verbatim
- No quoted phrases longer than 8 words
- Attribute claims inline: "Reuters के अनुसार...", "BBC के मुताबिक..."
- Use natural Hindi journalistic register. Avoid heavy Sanskritized vocabulary; aim for the style of BBC Hindi or The Wire Hindi.
- Do not invent facts. If sources disagree, note the disagreement
- Do not include opinions, predictions, or editorial commentary
- Title: 6–14 words

SOURCES:
{sources_block}

Output JSON only. No prose before or after."""


async def write_article(cluster_id: int) -> ArticleDraft | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(RawItem, Source)
            .select_from(RawItem)
            .join(Source, RawItem.source_id == Source.id)
            .where(RawItem.cluster_id == cluster_id)
            .order_by(Source.authority.desc())
        )
        rows = list(result.fetchall())

    if not rows:
        return None

    seen_sources = set()
    items = []
    sources_info = []
    for raw_item, source in rows:
        if source.id in seen_sources:
            continue
        seen_sources.add(source.id)
        items.append({
            "title": raw_item.title,
            "body": raw_item.body,
            "snippet": raw_item.snippet,
            "url": raw_item.url,
            "published_at": raw_item.published_at,
        })
        sources_info.append({
            "name": source.name,
            "authority": float(source.authority),
            "url": raw_item.url,
        })
        if len(items) >= 6:
            break

    sources_block = _build_sources_block(items, sources_info)

    draft = ArticleDraft(title_en="", body_en="", title_hi=None, body_hi=None)

    for lang, prompt_template in [("en", EN_WRITER_PROMPT), ("hi", HI_WRITER_PROMPT)]:
        prompt = prompt_template.format(sources_block=sources_block)
        result_json = None
        try:
            result_json = _call_gemini(prompt)
            log.info(f"LLM_USAGE provider=gemini model={settings.GEMINI_MODEL_TEXT} cluster_id={cluster_id} lang={lang}")
        except Exception as e:
            log.warning(f"Gemini call failed for cluster {cluster_id} lang={lang}: {e}, trying Anthropic")
            try:
                result_json = _call_anthropic(prompt)
                log.info(f"LLM_USAGE provider=anthropic model={settings.ANTHROPIC_MODEL} cluster_id={cluster_id} lang={lang}")
            except Exception as e2:
                log.error(f"Anthropic fallback also failed for cluster {cluster_id} lang={lang}: {e2}")
                continue

        if result_json:
            title = result_json.get("title", "")
            body = result_json.get("body", "")
            if lang == "en":
                draft.title_en = title
                draft.body_en = body
            else:
                draft.title_hi = title
                draft.body_hi = body

    if not draft.body_en:
        return None
    return draft
