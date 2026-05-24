import logging

import httpx
import trafilatura
import langid

from buzz_news.config import get_settings
from buzz_news.openclaw_client import call_skill
from buzz_news.sources.base import RawCandidate

settings = get_settings()
log = logging.getLogger("buzz_news.normalizer")


async def normalize(source, candidate: RawCandidate, http: httpx.AsyncClient) -> dict:
    body = None
    detected_lang = candidate.language or source.language

    try:
        resp = await http.get(candidate.url, timeout=10.0, follow_redirects=True)
        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type or not content_type:
            body = trafilatura.extract(resp.text)
        else:
            body = resp.text[:5000] if resp.text else None
    except Exception as e:
        log.warning(f"Failed to fetch {candidate.url}: {e}")

    use_browser = (
        settings.OPENCLAW_BROWSER_FALLBACK_ENABLED
        and source.extra.get("js_heavy", False)
        and (not body or len(body or "") < 200)
    )

    if use_browser:
        try:
            result = await call_skill(
                "skills/agent-browser-clawdbot/extract",
                {"url": candidate.url},
            )
            body = result.get("content") or result.get("text") or body
        except Exception as e:
            log.warning(f"Browser fallback failed for {candidate.url}: {e}")

    if not body or len(body or "") < 200:
        body = candidate.snippet

    if body:
        lang, prob = langid.classify(body)
        if prob < -50:
            detected_lang = candidate.language or source.language
        else:
            detected_lang = lang

    return {
        "body": body,
        "language": detected_lang,
    }
