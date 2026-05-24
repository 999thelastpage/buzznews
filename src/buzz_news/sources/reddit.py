import logging
from datetime import datetime, timezone

import httpx

from buzz_news.sources.base import RawCandidate
from buzz_news.models import Source
from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.sources.reddit")


class RateLimitError(Exception):
    pass


class RedditAdapter:
    USER_AGENT = settings.REDDIT_USER_AGENT

    async def fetch(self, source: Source, http: httpx.AsyncClient) -> list[RawCandidate]:
        try:
            resp = await http.get(
                source.url,
                headers={"User-Agent": self.USER_AGENT},
                timeout=15.0,
            )
        except Exception:
            raise

        if resp.status_code == 429:
            raise RateLimitError(f"Reddit rate limited: {source.url}")
        if resp.status_code == 403:
            raise RateLimitError(f"Reddit 403 (country blocked?): {source.url}")
        if resp.status_code >= 400:
            raise Exception(f"Reddit fetch failed {resp.status_code}: {source.url}")

        data = resp.json()
        children = data.get("data", {}).get("children", [])
        candidates = []
        for item in children:
            post = item.get("data", {})
            if not post.get("url"):
                continue
            created_utc = post.get("created_utc", 0)
            published = datetime.fromtimestamp(created_utc, tz=timezone.utc) if created_utc else None
            candidates.append(RawCandidate(
                external_id=post.get("permalink", ""),
                url=post["url"],
                title=post.get("title", "No title"),
                snippet=post.get("selftext") or None,
                published_at=published,
                language=source.language,
            ))

        return candidates
