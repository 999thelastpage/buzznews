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


async def _search_unsplash(keywords: list[str]) -> tuple[str | None, str | None]:
    if not settings.UNSPLASH_ACCESS_KEY:
        return None, None
    query = " ".join(keywords[:3])
    url = f"https://api.unsplash.com/search/photos?query={query}&per_page=5&orientation=landscape"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Client-ID {settings.UNSPLASH_ACCESS_KEY}"},
            )
            if resp.status_code != 200:
                return None, None
            data = resp.json()
            results = data.get("results", [])
            for item in results:
                urls = item.get("urls", {})
                thumb = urls.get("thumb") or urls.get("small") or urls.get("regular")
                credit = f"Photo by {item.get('user', {}).get('name', 'Unsplash')}"
                if thumb:
                    return thumb, credit
    except Exception as e:
        log.warning(f"Unsplash search failed: {e}")
    return None, None


async def _search_pexels(keywords: list[str]) -> tuple[str | None, str | None]:
    if not settings.PEXELS_API_KEY:
        return None, None
    query = " ".join(keywords[:3])
    url = f"https://api.pexels.com/v1/search?query={query}&per_page=5&orientation=landscape"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": settings.PEXELS_API_KEY},
            )
            if resp.status_code != 200:
                return None, None
            data = resp.json()
            photos = data.get("photos", [])
            for item in photos:
                src = item.get("src", {})
                thumb = src.get("large") or src.get("medium") or src.get("original")
                credit = f"Photo by {item.get('photographer', 'Pexels')}"
                if thumb:
                    return thumb, credit
    except Exception as e:
        log.warning(f"Pexels search failed: {e}")
    return None, None


async def _search_wikimedia(keywords: list[str]) -> tuple[str | None, str | None]:
    query = "_".join(keywords[:3])
    url = (
        f"https://en.wikipedia.org/w/api.php"
        f"?action=query&list=search&srsearch={query}&format=json&srlimit=5"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None, None
            data = resp.json()
            pages = data.get("query", {}).get("search", [])
            for page in pages:
                title = page.get("title", "")
                img_url = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
                return img_url, f"Wikimedia Commons / {title}"
    except Exception as e:
        log.warning(f"Wikimedia search failed: {e}")
    return None, None


def _download_and_resize(image_url: str, sizes: list[tuple[int, int]], out_dir: Path) -> dict[str, str]:
    paths = {}
    try:
        resp = httpx.get(image_url, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        original = Image.open(BytesIO(resp.content)).convert("RGB")
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
) -> tuple[str | None, str | None]:
    static_dir = Path(settings.STATIC_DIR)
    out_dir = static_dir / "images" / str(article_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    keywords = _extract_keywords(title, body)
    if not keywords:
        return None, None

    image_url = None
    credit = None

    image_url, credit = await _search_unsplash(keywords)
    if image_url:
        log.info(f"Image from Unsplash for article {article_id}")

    if not image_url:
        image_url, credit = await _search_pexels(keywords)
        if image_url:
            log.info(f"Image from Pexels for article {article_id}")

    if not image_url:
        image_url, credit = await _search_wikimedia(keywords)
        if image_url:
            log.info(f"Image from Wikimedia for article {article_id}")

    if not image_url:
        log.info(f"No image found for article {article_id}")
        return None, None

    paths = _download_and_resize(image_url, [HERO_SIZE, CARD_SIZE, THUMB_SIZE], out_dir)
    hero_path = paths.get("hero")
    if hero_path:
        return hero_path, credit
    return None, None
