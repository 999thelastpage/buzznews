import logging
from datetime import datetime, timezone

import httpx

from buzz_news.sources.base import RawCandidate
from buzz_news.models import Source
from buzz_news.config import get_settings
from buzz_news.openclaw_client import call_skill

settings = get_settings()
log = logging.getLogger("buzz_news.sources.tavily")


class TavilyAdapter:
    async def fetch(self, source: Source, http: httpx.AsyncClient) -> list[RawCandidate]:
        extra = source.extra or {}
        query = extra.get("query", "")
        max_results = extra.get("max_results", 20)
        if not query:
            log.warning(f"Tavily source {source.slug} has no query, skipping")
            return []

        now = datetime.now(timezone.utc)
        if source.last_fetched_at:
            cadence = extra.get("cadence_minutes", 90)
            age = (now - source.last_fetched_at).total_seconds() / 60
            if age < cadence:
                log.info(f"Tavily source {source.slug} skipped: {age:.0f}m since last fetch (< {cadence}m cadence)")
                return []

        try:
            result = await call_skill(
                "skills/openclaw-tavily-search/search",
                {"query": query, "max_results": max_results},
            )
        except Exception as e:
            log.error(f"Tavily skill call failed for {source.slug}: {e}")
            raise

        articles = result.get("results", [])
        candidates = []
        for item in articles:
            url = item.get("url") or ""
            if not url:
                continue
            raw_date = item.get("published_date") or ""
            published = None
            if raw_date:
                try:
                    published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                except Exception:
                    pass
            candidates.append(RawCandidate(
                external_id=url,
                url=url,
                title=item.get("title", "No title") or "No title",
                snippet=item.get("content") or None,
                published_at=published,
                language=source.language,
            ))

        return candidates
