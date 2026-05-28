import logging
import re
from pathlib import Path

import httpx
from PIL import Image
from io import BytesIO

from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.imager")

HERO_SIZE = (1200, 675)
CARD_SIZE = (600, 338)
THUMB_SIZE = (240, 135)

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "must", "shall",
}


def _extract_keywords(title: str, body: str = "", max_keywords: int = 5) -> list[str]:
    text = f"{title} {body or ''}".lower()
    words = re.findall(r"[a-z]+", text)
    keywords = [w for w in words if w not in STOPWORDS and len(w) > 3]
    freq: dict[str, int] = {}
    for w in keywords:
        freq[w] = freq.get(w, 0) + 1
    sorted_keywords = sorted(freq.items(), key=lambda x: -x[1])
    return [k for k, _ in sorted_keywords[:max_keywords]]


# Generic, safe visual queries per article category. Used as the fallback when
# the writer didn't emit an image_query (off-enum, omitted, or all LLM
# providers failed). These deliberately describe the activity/setting — never
# proper nouns — so stock libraries return on-topic photos.
CATEGORY_QUERIES = {
    "sports": "sports stadium athletes competition",
    "politics": "government parliament building flag",
    "international": "world diplomacy flags meeting",
    "business": "business finance office skyline",
    "tech": "technology computer circuit data",
    "culture": "concert festival arts performance",
    "general": "city skyline street newspaper",
}


def _tokens(text: str | None) -> set[str]:
    """Lowercased content tokens (>2 chars, no stopwords) for overlap checks."""
    if not text:
        return set()
    return {
        w for w in re.findall(r"[a-z]+", text.lower())
        if w not in STOPWORDS and len(w) > 2
    }


def _is_relevant(result_text: str | None, query_terms: set[str]) -> bool:
    """True if a candidate image's own description/tags share at least one
    content token with the query. When there are no query terms to judge by, or
    the candidate carries no describable text, accept (can't disprove). The
    guard's job is to reject confidently-wrong matches (a bowling photo for a
    football story), not to demand a perfect caption match."""
    if not query_terms:
        return True
    result_terms = _tokens(result_text)
    if not result_terms:
        return True
    return bool(result_terms & query_terms)


def _build_query(
    image_query: str | None,
    category: str | None,
    title: str,
    body: str,
) -> tuple[str, set[str]]:
    """Choose the stock-photo search query and the terms the relevance guard
    judges candidates against. Priority: the writer's literal visual query →
    a generic per-category query → legacy frequency keywords."""
    if image_query and image_query.strip():
        terms = _tokens(image_query) | _tokens(category)
        return image_query.strip(), terms
    if category and category in CATEGORY_QUERIES:
        q = CATEGORY_QUERIES[category]
        return q, _tokens(q)
    keywords = _extract_keywords(title, body)
    return " ".join(keywords[:3]), set(keywords)


async def _search_unsplash(query: str, query_terms: set[str]) -> tuple[str | None, str | None]:
    if not settings.UNSPLASH_ACCESS_KEY:
        return None, None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.unsplash.com/search/photos",
                params={"query": query, "per_page": 8, "orientation": "landscape"},
                headers={"Authorization": f"Client-ID {settings.UNSPLASH_ACCESS_KEY}"},
            )
            if resp.status_code != 200:
                return None, None
            data = resp.json()
            results = data.get("results", [])
            for item in results:
                # Relevance guard: skip a candidate whose own description/tags
                # share nothing with the query.
                desc = " ".join(filter(None, [
                    item.get("alt_description"),
                    item.get("description"),
                    " ".join(t.get("title", "") for t in (item.get("tags") or [])),
                ]))
                if not _is_relevant(desc, query_terms):
                    continue
                urls = item.get("urls", {})
                # `raw` is the original; append CDN params to cap at 1600px wide
                # for our 1200px hero target. `full` (~2048px) and `regular`
                # (1080px) are fallbacks. Never use `small`/`thumb` (200px).
                raw = urls.get("raw")
                if raw:
                    image_url = f"{raw}&w=1600&fit=max&q=85" if "?" in raw else f"{raw}?w=1600&fit=max&q=85"
                else:
                    image_url = urls.get("full") or urls.get("regular")
                credit = f"Photo by {item.get('user', {}).get('name', 'Unsplash')}"
                if image_url:
                    return image_url, credit
    except Exception as e:
        log.warning(f"Unsplash search failed: {e}")
    return None, None


async def _search_pexels(query: str, query_terms: set[str]) -> tuple[str | None, str | None]:
    if not settings.PEXELS_API_KEY:
        return None, None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": 8, "orientation": "landscape"},
                headers={"Authorization": settings.PEXELS_API_KEY},
            )
            if resp.status_code != 200:
                return None, None
            data = resp.json()
            photos = data.get("photos", [])
            for item in photos:
                if not _is_relevant(item.get("alt"), query_terms):
                    continue
                src = item.get("src", {})
                # `large2x` is 1880px wide; `original` is the source upload.
                # Avoid `large` (940px) and `medium` (350px) — too small for hero.
                image_url = src.get("large2x") or src.get("original")
                credit = f"Photo by {item.get('photographer', 'Pexels')}"
                if image_url:
                    return image_url, credit
    except Exception as e:
        log.warning(f"Pexels search failed: {e}")
    return None, None


async def _search_wikimedia(query: str, query_terms: set[str]) -> tuple[str | None, str | None]:
    """Search Wikipedia for an article matching the query and return its lead
    image. Uses `generator=search` + `prop=pageimages` to get the actual image
    URL in a single round-trip. Returns (image_url, credit) or (None, None)."""
    headers = {
        # Wikipedia API requires a descriptive User-Agent per their policy;
        # bare httpx defaults get 403'd.
        "User-Agent": "BuzzNewsBot/1.0 (https://slow.myvnc.com; contact via repo)",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": query,
                    "gsrlimit": 5,
                    "prop": "pageimages",
                    "piprop": "original",
                    "format": "json",
                },
            )
            if resp.status_code != 200:
                return None, None
            data = resp.json()
            pages = data.get("query", {}).get("pages", {}) or {}
            # Pages come back as a dict keyed by page id; order by gsrindex.
            ordered = sorted(pages.values(), key=lambda p: p.get("index", 999))
            for page in ordered:
                title = page.get("title", "")
                if not _is_relevant(title, query_terms):
                    continue
                original = page.get("original") or {}
                src = original.get("source")
                if src:
                    return src, f"Wikimedia Commons / {title}"
    except Exception as e:
        log.warning(f"Wikimedia search failed: {e}")
    return None, None


def _download_and_resize(image_url: str, sizes: list[tuple[int, int]], out_dir: Path) -> dict[str, str]:
    paths = {}
    try:
        resp = httpx.get(image_url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        original = Image.open(BytesIO(resp.content)).convert("RGB")
        # Reject sources too small to make a sharp hero. PIL.thumbnail() only
        # shrinks, so a 200x133 source stays 200x133 and renders blurry when
        # the browser stretches it into a 16:9 box.
        if original.width < 800:
            log.warning(
                f"Image source too small ({original.width}x{original.height}) for {image_url}; skipping"
            )
            return paths
        for name, size in [("hero", HERO_SIZE), ("card", CARD_SIZE), ("thumb", THUMB_SIZE)]:
            resized = original.copy()
            resized.thumbnail(size, Image.LANCZOS)
            out_path = out_dir / f"{name}.webp"
            resized.save(out_path, "WEBP", quality=85)
            paths[name] = str(out_path)
    except Exception as e:
        log.warning(f"Image download/resize failed for {image_url}: {e}")
    return paths


async def pick_image(
    article_id: int,
    title: str,
    body: str = "",
    image_query: str | None = None,
    category: str | None = None,
) -> tuple[str | None, str | None]:
    static_dir = Path(settings.STATIC_DIR)
    out_dir = static_dir / "images" / str(article_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    # The writer's literal visual query drives the search; category is the
    # fallback anchor; frequency keywords are the last resort. The relevance
    # guard rejects candidates whose own description shares nothing with it.
    query, query_terms = _build_query(image_query, category, title, body)
    if not query.strip():
        return None, None
    log.info(
        f"Image search for article {article_id}: query={query!r} "
        f"terms={sorted(query_terms)}"
    )

    image_url = None
    credit = None

    image_url, credit = await _search_unsplash(query, query_terms)
    if image_url:
        log.info(f"Image from Unsplash for article {article_id}")

    if not image_url:
        image_url, credit = await _search_pexels(query, query_terms)
        if image_url:
            log.info(f"Image from Pexels for article {article_id}")

    if not image_url:
        image_url, credit = await _search_wikimedia(query, query_terms)
        if image_url:
            log.info(f"Image from Wikimedia for article {article_id}")

    if not image_url:
        log.info(f"No relevant image found for article {article_id} (query={query!r})")
        return None, None

    paths = _download_and_resize(image_url, [HERO_SIZE, CARD_SIZE, THUMB_SIZE], out_dir)
    hero_path = paths.get("hero")
    if hero_path:
        # Return a web-relative URL (Caddy serves from STATIC_DIR root),
        # not the on-disk path -- otherwise <img src="/var/lib/..."> 404s.
        hero_url = f"/images/{article_id}/{Path(hero_path).name}"
        return hero_url, credit
    return None, None
