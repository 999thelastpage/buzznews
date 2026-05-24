import logging
import time
from datetime import datetime, timezone

import feedparser
import httpx

from buzz_news.sources.base import RawCandidate
from buzz_news.models import Source

log = logging.getLogger("buzz_news.sources.rss")


class RateLimitError(Exception):
    pass


class RSSAdapter:
    async def fetch(self, source: Source, http: httpx.AsyncClient) -> list[RawCandidate]:
        headers = {}
        if source.last_etag:
            headers["If-None-Match"] = source.last_etag
        if source.last_modified:
            headers["If-Modified-Since"] = source.last_modified

        try:
            resp = await http.get(source.url, headers=headers, timeout=15.0, follow_redirects=True)
        except Exception:
            raise

        if resp.status_code == 304:
            return []
        if resp.status_code == 429:
            raise RateLimitError(f"RSS rate limited: {source.url}")
        if resp.status_code >= 400:
            raise Exception(f"RSS fetch failed {resp.status_code}: {source.url}")

        feed = feedparser.parse(resp.content)
        candidates = []
        for entry in feed.entries:
            if not entry.get("link"):
                continue
            published = None
            if entry.get("published_parsed"):
                try:
                    t = time.mktime(entry.published_parsed)
                    published = datetime.fromtimestamp(t, tz=timezone.utc)
                except Exception:
                    pass
            elif entry.get("updated_parsed"):
                try:
                    t = time.mktime(entry.updated_parsed)
                    published = datetime.fromtimestamp(t, tz=timezone.utc)
                except Exception:
                    pass

            candidates.append(RawCandidate(
                external_id=entry.get("id") or entry.link,
                url=entry.link,
                title=entry.title or "No title",
                snippet=entry.get("summary") or entry.get("description") or None,
                published_at=published,
                language=source.language,
            ))

        return candidates
