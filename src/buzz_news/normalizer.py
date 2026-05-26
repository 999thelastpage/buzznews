import logging

import httpx
import trafilatura
import langid

from buzz_news.config import get_settings
from buzz_news.openclaw_client import call_skill
from buzz_news.sources import SKIP_BODY_FETCH_KINDS
from buzz_news.sources.base import RawCandidate

settings = get_settings()
log = logging.getLogger("buzz_news.normalizer")


# Phrases trafilatura returns when it hit a paywall / scraper-block page
# instead of an actual article. Storing these as the "body" poisons the
# clusterer + writer downstream (e.g. NDTV was producing 97% paywall
# bodies of ~282 chars each). We detect and drop to None so the
# snippet falls through. Keep this list narrow — false positives demote
# legitimate articles to snippet-only.
_PAYWALL_MARKERS = (
    "access denied",
    "you don't have permission to access",
    "you don’t have permission to access",
    "subscribe to continue",
    "subscribe to read",
    "sign in to read",
    "sign in to continue",
    "this content is for subscribers",
    "members-only content",
    "become a member to read",
    "to continue reading, please",
    "to read this article",
    "please enable javascript",
    "please enable cookies",
    "are you a robot",
    "checking your browser",
    "verifying you are human",
)


def _looks_paywall_block(text: str | None) -> bool:
    """Return True if `text` looks like a paywall / scraper-block page
    rather than article content. Trafilatura extracts the visible text
    from whatever HTML it gets back — when a publisher serves a block
    page, that page's text ends up here. Length cap + marker match
    keeps the check conservative."""
    if not text:
        return False
    if len(text) > 1500:
        # Real articles can mention "sign in" in passing; only consider
        # block pages, which are short.
        return False
    low = text.lower()
    return any(marker in low for marker in _PAYWALL_MARKERS)


async def normalize(source, candidate: RawCandidate, http: httpx.AsyncClient) -> dict:
    body = None
    detected_lang = candidate.language or source.language

    # Some source kinds (e.g. Google News) hand us URLs that don't resolve
    # to article content server-side. Skipping the trafilatura step keeps
    # them as title/snippet-only signals and saves a wasted HTTP call.
    skip_fetch = source.kind in SKIP_BODY_FETCH_KINDS

    if not skip_fetch:
        try:
            resp = await http.get(candidate.url, timeout=10.0, follow_redirects=True)
            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type or not content_type:
                body = trafilatura.extract(resp.text)
            else:
                body = resp.text[:5000] if resp.text else None
        except Exception as e:
            log.warning(f"Failed to fetch {candidate.url}: {e}")

    # If trafilatura extracted a paywall / block page instead of an
    # article, treat the body as missing so the snippet path takes over
    # downstream. Logged at INFO so the rate is visible in the worker
    # log (NDTV in particular trips this on ~90% of fetches).
    if _looks_paywall_block(body):
        log.info(f"paywall/block body detected, dropping ({source.name}): {candidate.url}")
        body = None

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
