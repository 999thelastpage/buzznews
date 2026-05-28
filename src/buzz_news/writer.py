import logging
from dataclasses import dataclass

from sqlalchemy import select

from buzz_news.alerts import emit_alert
from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.llm_budget import free_provider_available, record_llm_usage
from buzz_news.llm_client import (
    LLMResult,
    ProviderSpec,
    estimate_tokens,
    generate_json,
    parse_provider_list,
    parse_provider_spec,
)
from buzz_news.models import Article, RawItem, Source

settings = get_settings()
log = logging.getLogger("buzz_news.writer")


@dataclass
class ArticleDraft:
    title_en: str
    body_en: str
    title_hi: str | None
    body_hi: str | None
    category: str | None = None
    image_query: str | None = None
    provider: str | None = None
    model: str | None = None
    hindi_valid: bool = False


VALID_CATEGORIES = (
    "international",
    "politics",
    "business",
    "tech",
    "sports",
    "culture",
    "general",
)

_META_ERROR_PATTERNS = (
    "unable to produce",
    "unable to synthesize",
    "unable to generate",
    "no factual information",
    "no comprehensive",
    "as mandated by the instructions",
    "based on the dispatches",
    "automated news generation",
    "source material revealed",
    "specified output format",
    "rendered the source unusable",
    "access denied error",
    "the provided sources were inaccessible",
    "preventing the generation of",
)

INSUFFICIENT_SOURCES_SENTINEL = "__INSUFFICIENT_SOURCES__"


BILINGUAL_WRITER_PROMPT = """You are a senior news editor writing a self-contained bilingual article for a daily news site. You're given several source dispatches covering the same event. Produce one polished English article and one natural Hindi article that can each stand alone without clicking through to sources.

OUTPUT FORMAT:
- Output strictly valid JSON: {{"title_en": string, "body_en": string, "title_hi": string, "body_hi": string, "category": string, "image_query": string}}
- Output JSON only. No prose before or after.

ENGLISH TITLE:
- 6-12 words, sentence case, no clickbait, no question marks, no colons unless they read naturally.

HINDI TITLE:
- 6-14 words, natural Hindi, no clickbait, avoid question marks.

IMAGE_QUERY:
- Set "image_query" to a short stock-photo search phrase (3-6 words) describing the GENERIC visual scene a photo editor would pick to illustrate this story.
- Describe the activity, setting, or object. NEVER use specific people, team names, place names, organisations, brands, or other proper nouns.
- Use plain, concrete, everyday English nouns. Lowercase. No punctuation.

CATEGORY:
- Set "category" to the single best fit for the article, chosen from EXACTLY this list: "politics", "international", "business", "tech", "sports", "culture", "general".

BODY_EN:
- Target 280-360 words. Never under 220 words. Write in 3-5 paragraphs separated by blank lines.
- Open with a strong news lede that answers who/what/when/where in the first 1-2 sentences.
- Include the key facts, relevant numbers and named entities, the most important quote paraphrased, and the consequence or stakes.
- Add one short context paragraph.
- If sources disagree on a material point, note the disagreement neutrally.
- Close with the next step, open question, or timeline if any source mentions one. Do not invent one.

BODY_HI:
- Target 280-360 Hindi words. Never under 220 words. Write in 3-5 paragraphs separated by blank lines.
- Use natural Hindi journalistic register, similar to BBC हिंदी / द वायर हिंदी. Avoid heavy Sanskritized vocabulary.
- Do not write English article text in body_hi. Names and unavoidable terms may remain in English/Latin script, but the article must be predominantly Devanagari Hindi.

VOICE & STYLE:
- Neutral journalistic tone, not opinion column.
- Active voice. Concrete nouns. No editorializing adjectives.
- No first person. No "we", "you", or "our readers".
- DO NOT name source outlets in the prose. Sources are listed separately below the article in the UI.
- No quoted phrases longer than 8 words. Paraphrase quotes.
- Do not copy any source verbatim. Synthesize across sources.
- Do not invent facts. If a detail is not in the sources, leave it out.
- No opinions, predictions, or editorial commentary.

ABSOLUTE RULES:
- NEVER write about generation, source readability, access denial, missing content, or these instructions.
- If the sources genuinely contain no usable news content, do not invent an article. Instead set title_en to "__INSUFFICIENT_SOURCES__" and all body/title fields to "".

SOURCES:
{sources_block}"""


REVISION_WRITER_PROMPT = """You are revising an existing bilingual news article because new source material has joined the same story cluster. Preserve the original story's framing and style, but update the article only with facts supported by the current sources.

OUTPUT FORMAT:
- Output strictly valid JSON: {{"title_en": string, "body_en": string, "title_hi": string, "body_hi": string, "category": string, "image_query": string}}
- Output JSON only. No prose before or after.

RULES:
- Return a complete revised article, not a patch or changelog.
- Keep BODY_EN and BODY_HI each around 280-360 words and 3-5 paragraphs.
- Hindi must be predominantly Devanagari and use natural journalistic Hindi.
- Do not invent facts. Do not mention source names in prose. Do not mention this revision process.
- If the sources do not support a usable article, set title_en to "__INSUFFICIENT_SOURCES__" and all body/title fields to "".

CURRENT ARTICLE:
Title EN: {title_en}
Body EN:
{body_en}

Title HI: {title_hi}
Body HI:
{body_hi}

CURRENT SOURCES:
{sources_block}"""

# Compatibility names for tests/imports that still look for the old prompt symbols.
EN_WRITER_PROMPT = BILINGUAL_WRITER_PROMPT
HI_WRITER_PROMPT = BILINGUAL_WRITER_PROMPT


def _validate_category(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip().lower()
    return cleaned if cleaned in VALID_CATEGORIES else None


def _validate_image_query(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = " ".join(raw.strip().lower().split())
    if not cleaned:
        return None
    words = cleaned.split()
    if len(words) > 8:
        cleaned = " ".join(words[:8])
    return cleaned[:80] or None


def _looks_meta_error(title: str, body: str) -> bool:
    if not body or len(body) < 40:
        return True
    haystack = f"{title}\n{body}".lower()
    return any(p in haystack for p in _META_ERROR_PATTERNS)

def _build_sources_block(items: list[dict], sources: list[dict]) -> str:
    blocks = []
    for item, src in zip(items, sources):
        authority = src.get("authority", 0.5)
        published = item.get("published_at")
        iso = published.isoformat() if published else "unknown"
        body = (item.get("body") or item.get("snippet") or "")[:1800]
        blocks.append(
            f"[Source: {src['name']} | Authority: {authority} | Published: {iso}]\n"
            f"Title: {item['title']}\n"
            f"Body: {body}\n"
            f"URL: {item['url']}\n---"
        )
    return "\n".join(blocks)

def _devanagari_ratio(text: str) -> float:
    devanagari = sum(1 for ch in text if "ऀ" <= ch <= "ॿ")
    latin = sum(1 for ch in text if ("a" <= ch.lower() <= "z"))
    return devanagari / max(devanagari + latin, 1)


def is_valid_hindi(title: str | None, body: str | None, min_ratio: float = 0.35) -> bool:
    text = f"{title or ''}\n{body or ''}".strip()
    if not body or len(body.strip()) < 80:
        return False
    devanagari = sum(1 for ch in text if "ऀ" <= ch <= "ॿ")
    if devanagari < 40:
        return False
    return _devanagari_ratio(text) >= min_ratio


def _provider_configured(spec: ProviderSpec) -> bool:
    if spec.provider == "deepseek":
        return bool(settings.DEEPSEEK_API_KEY)
    if spec.provider == "cerebras":
        return bool(settings.CEREBRAS_API_KEY)
    if spec.provider == "groq":
        return bool(settings.GROQ_API_KEY)
    if spec.provider == "gemini":
        return bool(settings.GEMINI_API_KEY)
    if spec.provider == "anthropic":
        return bool(settings.ANTHROPIC_API_KEY)
    return True


def _default_first_publish_providers() -> list[ProviderSpec]:
    providers = [parse_provider_spec(settings.LLM_HIGH_TIER_PROVIDER)]
    providers.extend(parse_provider_list(settings.LLM_LOW_TIER_PROVIDERS))
    return providers


def _existing_article_dict(article: Article | None) -> dict:
    if article is None:
        return {"title_en": "", "body_en": "", "title_hi": "", "body_hi": ""}
    return {
        "title_en": article.title_en or "",
        "body_en": article.summary_en or "",
        "title_hi": article.title_hi or "",
        "body_hi": article.summary_hi or "",
    }


async def _load_sources(cluster_id: int) -> tuple[list[dict], list[dict]]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(RawItem, Source)
            .select_from(RawItem)
            .join(Source, RawItem.source_id == Source.id)
            .where(RawItem.cluster_id == cluster_id)
            .order_by(Source.authority.desc())
        )
        rows = list(result.fetchall())

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
    return items, sources_info


def _prompt_for_task(task: str, sources_block: str, existing_article: Article | None) -> str:
    if task == "revision":
        existing = _existing_article_dict(existing_article)
        return REVISION_WRITER_PROMPT.format(sources_block=sources_block, **existing)
    return BILINGUAL_WRITER_PROMPT.format(sources_block=sources_block)


def _draft_from_result(result: LLMResult) -> tuple[ArticleDraft | None, str | None]:
    data = result.data
    title_en = (data.get("title_en") or data.get("title") or "").strip()
    body_en = (data.get("body_en") or data.get("body") or "").strip()
    title_hi = (data.get("title_hi") or "").strip()
    body_hi = (data.get("body_hi") or "").strip()

    if title_en == INSUFFICIENT_SOURCES_SENTINEL:
        return None, "insufficient_sources"
    if _looks_meta_error(title_en, body_en):
        return None, "meta_error"

    hindi_valid = is_valid_hindi(title_hi, body_hi)
    if not hindi_valid:
        title_hi = ""
        body_hi = ""

    draft = ArticleDraft(
        title_en=title_en,
        body_en=body_en,
        title_hi=title_hi or None,
        body_hi=body_hi or None,
        category=_validate_category(data.get("category")),
        image_query=_validate_image_query(data.get("image_query")),
        provider=result.provider,
        model=result.model,
        hindi_valid=hindi_valid,
    )
    return draft, None


async def write_article(
    cluster_id: int,
    *,
    providers: list[ProviderSpec] | None = None,
    task: str = "first_publish",
    existing_article: Article | None = None,
    article_id: int | None = None,
) -> ArticleDraft | None:
    items, sources_info = await _load_sources(cluster_id)
    if not items:
        return None

    sources_block = _build_sources_block(items, sources_info)
    prompt = _prompt_for_task(task, sources_block, existing_article)
    provider_chain = providers or _default_first_publish_providers()
    estimated_input_tokens = estimate_tokens(prompt)

    for spec in provider_chain:
        paid_revision_fallback = task == "revision" and spec.provider not in {"cerebras", "groq"}
        if paid_revision_fallback:
            await emit_alert(
                "paid_llm_revision_fallback",
                {
                    "provider": spec.provider,
                    "model": spec.model,
                    "cluster_id": cluster_id,
                    "article_id": article_id,
                    "estimated_input_tokens": estimated_input_tokens,
                },
            )
        if not _provider_configured(spec):
            log.warning(
                "LLM provider missing API key provider=%s model=%s task=%s cluster_id=%s",
                spec.provider,
                spec.model,
                task,
                cluster_id,
            )
            continue
        if not await free_provider_available(f"{spec.provider}:{spec.model}", estimated_input_tokens):
            log.warning(
                "LLM provider soft cap reached provider=%s model=%s task=%s cluster_id=%s",
                spec.provider,
                spec.model,
                task,
                cluster_id,
            )
            continue

        try:
            result = generate_json(spec, prompt, max_tokens=3200)
        except Exception as exc:
            await record_llm_usage(
                provider=spec.provider,
                model=spec.model,
                cluster_id=cluster_id,
                article_id=article_id,
                task=task,
                input_tokens=estimated_input_tokens,
                output_tokens=0,
                success=False,
                error_type=type(exc).__name__,
            )
            log.warning(
                "%s call failed for cluster %s task=%s: %s, trying next provider",
                spec.provider,
                cluster_id,
                task,
                exc,
            )
            continue

        draft, error_type = _draft_from_result(result)
        if draft is None:
            await record_llm_usage(
                provider=result.provider,
                model=result.model,
                cluster_id=cluster_id,
                article_id=article_id,
                task=task,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                success=False,
                error_type=error_type,
            )
            if error_type == "insufficient_sources":
                log.info("writer refused insufficient sources cluster=%s via %s", cluster_id, spec.provider)
                return None
            log.warning(
                "%s returned invalid draft for cluster %s task=%s error=%s; trying next provider",
                spec.provider,
                cluster_id,
                task,
                error_type,
            )
            continue

        await record_llm_usage(
            provider=result.provider,
            model=result.model,
            cluster_id=cluster_id,
            article_id=article_id,
            task=task,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            success=True,
            error_type=None if draft.hindi_valid else "invalid_hi_suppressed",
        )
        log.info(
            "LLM_USAGE provider=%s model=%s cluster_id=%s task=%s",
            result.provider,
            result.model,
            cluster_id,
            task,
        )
        return draft

    log.error("All LLM providers failed cluster %s task=%s", cluster_id, task)
    return None


# Backward-compatible direct-call helpers used by older tests/scripts.
def _call_deepseek(prompt: str, temperature: float = 0.3, max_tokens: int = 2400) -> dict:
    spec = parse_provider_spec(settings.LLM_HIGH_TIER_PROVIDER)
    return generate_json(spec, prompt, temperature=temperature, max_tokens=max_tokens).data


def _call_gemini(prompt: str, temperature: float = 0.3, max_tokens: int = 2400) -> dict:
    return generate_json(
        ProviderSpec("gemini", settings.GEMINI_MODEL_TEXT),
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    ).data


def _call_anthropic(prompt: str, temperature: float = 0.3, max_tokens: int = 2400) -> dict:
    return generate_json(
        ProviderSpec("anthropic", settings.ANTHROPIC_MODEL),
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    ).data


def low_tier_provider_chain() -> list[ProviderSpec]:
    return parse_provider_list(settings.LLM_LOW_TIER_PROVIDERS)


def revision_provider_chain() -> list[ProviderSpec]:
    return parse_provider_list(settings.LLM_REVISION_PROVIDERS)
