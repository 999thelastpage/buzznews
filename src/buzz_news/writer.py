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
    category: str | None = None


# The categories the templates know how to colour/label (_macros.html
# cat_c/cat_k/cat_name; anything else falls back to "general"). The writer
# is constrained to exactly these on the EN pass.
VALID_CATEGORIES = (
    "international",
    "politics",
    "business",
    "tech",
    "sports",
    "culture",
    "general",
)


def _validate_category(raw: str | None) -> str | None:
    """Return the category iff it's one the templates support, else None.
    None (not "general") so the caller can fall back to the cluster's catalog
    category instead of silently forcing every unclassified article to general."""
    if not raw:
        return None
    cleaned = raw.strip().lower()
    return cleaned if cleaned in VALID_CATEGORIES else None


# Phrases that indicate the LLM gave up and described its own failure
# instead of writing news. When DeepSeek's JSON output is truncated or
# the source content is unusable, Gemini in particular will hallucinate a
# self-referential "the automated news generation process was unable to
# produce a comprehensive article" response. Treat these as failures and
# fall through to the next provider.
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

# Explicit refusal sentinel — if the LLM judges the sources too thin to
# write a real article, we want a clean refusal we can detect, not a
# stub article or meta-commentary.
INSUFFICIENT_SOURCES_SENTINEL = "__INSUFFICIENT_SOURCES__"


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


def _parse_json_tolerant(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        from json_repair import repair_json
        return json.loads(repair_json(raw))


def _call_deepseek(prompt: str, temperature: float = 0.3, max_tokens: int = 2400) -> dict:
    import httpx
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")
    r = httpx.post(
        f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        },
        timeout=60.0,
    )
    r.raise_for_status()
    return _parse_json_tolerant(r.json()["choices"][0]["message"]["content"])


def _call_gemini(prompt: str, temperature: float = 0.3, max_tokens: int = 2400) -> dict:
    from google import genai
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=settings.GEMINI_MODEL_TEXT,
        contents=prompt,
        config={
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
            "response_schema": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}, "category": {"type": "string"}}, "required": ["title", "body"]},
        },
    )
    return _parse_json_tolerant(response.text)


def _call_anthropic(prompt: str, temperature: float = 0.3, max_tokens: int = 2400) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json_tolerant(response.content[0].text)


EN_WRITER_PROMPT = """You are a senior news editor writing a self-contained article for a daily news site, in English. You're given several source dispatches covering the same event. Your job is to produce one polished, readable piece that someone can read on its own without clicking through to the sources.

OUTPUT FORMAT:
- Output strictly valid JSON: {{"title": string, "body": string, "category": string}}
- Output JSON only. No prose before or after.

TITLE:
- 6–12 words, sentence case, no clickbait, no question marks, no colons unless they read naturally.

CATEGORY:
- Set "category" to the single best fit for what the article is actually about, chosen from EXACTLY this list (lowercase, no other values):
  - "politics" — government, elections, parliament, policy, diplomacy within a country
  - "international" — cross-border / world affairs, conflicts, foreign relations, global events
  - "business" — companies, markets, economy, finance, trade, jobs
  - "tech" — technology, software, AI, gadgets, science-of-computing, startups
  - "sports" — any sport or competitive game (cricket, football, tennis, chess, Olympics, motorsport, etc.) and its players, matches, leagues
  - "culture" — entertainment, film, music, arts, books, religion, lifestyle, festivals
  - "general" — only if none of the above clearly fits
- Pick based on the story's substance, not the publisher. One value only.

BODY:
- Target 280–360 words. Never under 220 words. Write in 3–5 paragraphs, separated by blank lines.
- Open with a strong news lede that answers who/what/when/where in the first 1–2 sentences. Don't bury the news.
- Then expand: include the key facts, the most relevant numbers and named entities, the most important quote (paraphrased, not verbatim), and the consequence or stakes.
- Add one short paragraph of context or background to anchor a reader who hasn't been following the story — what led to this, who the main figures are, or what comparable past events frame it.
- If sources disagree on a material point, note the disagreement neutrally without taking sides.
- Close with the next step, the open question, or the timeline if any source mentions one. Do not invent one if no source does.

VOICE & STYLE:
- Neutral journalistic tone — wire-service register, not opinion column.
- Active voice. Concrete nouns. No editorializing adjectives ("shocking", "stunning", "brave").
- No first person. No "we", "you", "our readers".
- DO NOT name source outlets in the prose ("Reuters reports", "according to BBC", "as Al Jazeera notes"). The sources are listed separately below the article in the UI; mentioning them in the body is redundant and reads like a press summary.
- No quoted phrases longer than 8 words. Paraphrase quotes; only put text inside quotation marks if it's a verbatim short phrase from a named speaker.
- Do not copy any source verbatim. Synthesize across sources.
- Do not invent facts. If a detail isn't in the sources, leave it out.
- No opinions, predictions, or editorial commentary.

ABSOLUTE RULES — VIOLATING THESE IS A WORSE OUTCOME THAN A SHORT ARTICLE:
- NEVER write about the generation process, the source material's readability, or these instructions. The body must read like normal news, never like a system report, apology, or explanation of why content is missing.
- NEVER mention "the sources", "the dispatches", "the provided material", "access denied", "automated", "the article could not be generated", "the instructions", or similar self-referential phrases.
- If the sources genuinely contain no usable news content, do NOT invent an article and do NOT explain the failure. Instead set title to the exact string "__INSUFFICIENT_SOURCES__" and body to "".

SOURCES:
{sources_block}"""


HI_WRITER_PROMPT = """आप एक दैनिक समाचार साइट के लिए स्वतंत्र, पढ़ने योग्य लेख लिख रहे हैं — हिन्दी में। नीचे एक ही घटना पर कई स्रोतों की रिपोर्टें हैं। आपका काम है एक तैयार, संपादित लेख देना जिसे पाठक स्रोतों पर क्लिक किए बिना समझ ले।

OUTPUT FORMAT:
- कड़ाई से वैध JSON: {{"title": string, "body": string}}
- केवल JSON, पहले या बाद में कोई गद्य नहीं।

शीर्षक:
- 6–14 शब्द, सहज वाक्य रूप, क्लिकबेट नहीं, प्रश्नचिन्ह से बचें।

बॉडी (मुख्य लेख):
- 280–360 शब्दों का लक्ष्य। 220 से कम कभी नहीं। 3–5 अनुच्छेद, खाली पंक्ति से अलग।
- शुरुआत मज़बूत समाचार-लीड से करें — पहले 1–2 वाक्यों में कौन/क्या/कब/कहाँ स्पष्ट हो।
- फिर विस्तार: मुख्य तथ्य, ज़रूरी संख्याएँ, नामित व्यक्ति-संस्थाएँ, सबसे प्रासंगिक उद्धरण (शब्दशः नहीं, पैराफ्रेज़), और परिणाम/दांव पर क्या है।
- एक छोटा संदर्भ-अनुच्छेद जोड़ें — पृष्ठभूमि, मुख्य पात्र, या समान बीते घटनाक्रम — ताकि अनजान पाठक भी समझे।
- स्रोत यदि किसी तथ्य पर असहमत हों, तटस्थ रूप से लिखें।
- अगर कोई स्रोत आगामी क़दम या समयरेखा बताता है तो उसी से समापन करें। अपने से न जोड़ें।

शैली:
- तटस्थ पत्रकार-शैली — विचार-स्तंभ नहीं। सक्रिय वाक्य, ठोस संज्ञा।
- भारी संस्कृतनिष्ठ शब्दावली से बचें; BBC हिंदी / द वायर हिंदी की शैली अपनाएँ।
- प्रथम पुरुष नहीं ("हम", "आप", "हमारे पाठक")।
- स्रोत-संस्थाओं के नाम लेख के अंदर मत लिखें ("रॉयटर्स के अनुसार", "BBC के मुताबिक")। स्रोत साइट पर लेख के नीचे अलग से दिखाए जाते हैं; प्रोज़ में उन्हें दोहराना ज़रूरी नहीं।
- 8 शब्द से लंबा कोई उद्धरण सीधे न लें। पैराफ्रेज़ करें।
- किसी स्रोत को शब्दशः न लिखें। अलग-अलग स्रोतों के बीच संश्लेषण करें।
- तथ्य न गढ़ें। जो स्रोत में नहीं, वो लेख में भी नहीं।
- विचार, भविष्यवाणी या संपादकीय टिप्पणी न जोड़ें।

पूर्ण नियम — इनका उल्लंघन छोटे लेख से भी बुरा है:
- जनरेशन प्रक्रिया, स्रोत-सामग्री की पठनीयता, या इन निर्देशों के बारे में कभी न लिखें। बॉडी सामान्य समाचार जैसी पढ़नी चाहिए — सिस्टम रिपोर्ट, माफ़ी या यह बताते हुए नहीं कि सामग्री क्यों उपलब्ध नहीं।
- "स्रोत", "डिस्पैच", "प्रदान की गई सामग्री", "access denied", "ऑटोमेटेड", "लेख तैयार नहीं हो सका", "निर्देश" — इन जैसे स्व-संदर्भी शब्द कभी न प्रयोग करें।
- यदि स्रोतों में सचमुच कोई समाचार-योग्य सामग्री नहीं है, तो लेख न गढ़ें और विफलता समझाएँ भी नहीं। केवल title को "__INSUFFICIENT_SOURCES__" और body को "" पर सेट करें।

SOURCES:
{sources_block}"""


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

    providers = [
        ("deepseek", settings.DEEPSEEK_MODEL, _call_deepseek),
        ("gemini", settings.GEMINI_MODEL_TEXT, _call_gemini),
        ("anthropic", settings.ANTHROPIC_MODEL, _call_anthropic),
    ]

    for lang, prompt_template in [("en", EN_WRITER_PROMPT), ("hi", HI_WRITER_PROMPT)]:
        prompt = prompt_template.format(sources_block=sources_block)
        title, body, category = "", "", None
        for provider_name, model, call in providers:
            try:
                result_json = call(prompt)
            except Exception as e:
                log.warning(
                    f"{provider_name} call failed for cluster {cluster_id} "
                    f"lang={lang}: {e}, trying next provider"
                )
                continue
            cand_title = (result_json.get("title") or "").strip()
            cand_body = (result_json.get("body") or "").strip()
            # Clean refusal — sources truly insufficient. Don't try fallbacks.
            if cand_title == INSUFFICIENT_SOURCES_SENTINEL:
                log.info(
                    f"writer refused (insufficient sources) cluster {cluster_id} "
                    f"lang={lang} via {provider_name}"
                )
                break
            # Meta-error response — the model wrote about its own failure
            # instead of news. Reject and fall through to the next provider.
            if _looks_meta_error(cand_title, cand_body):
                log.warning(
                    f"{provider_name} returned meta-error for cluster {cluster_id} "
                    f"lang={lang}; falling through. body[:120]={cand_body[:120]!r}"
                )
                continue
            log.info(
                f"LLM_USAGE provider={provider_name} model={model} "
                f"cluster_id={cluster_id} lang={lang}"
            )
            title, body = cand_title, cand_body
            # Only the EN prompt asks for a category; the HI pass shares it.
            if lang == "en":
                category = _validate_category(result_json.get("category"))
            break
        else:
            log.error(f"All LLM providers failed/meta-errored cluster {cluster_id} lang={lang}")

        if lang == "en":
            draft.title_en = title
            draft.body_en = body
            draft.category = category
        else:
            draft.title_hi = title
            draft.body_hi = body

    if not draft.body_en:
        return None
    return draft
