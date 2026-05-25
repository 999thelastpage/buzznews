import logging
from datetime import datetime, timezone

import httpx

from buzz_news.sources.base import RawCandidate
from buzz_news.models import Source
from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.sources.tavily")

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class TavilyAdapter:
    async def fetch(self, source: Source, http: httpx.AsyncClient) -> list[RawCandidate]:
        extra = source.extra or {}
        query = extra.get("query", "")
        if not query:
            log.warning(f"Tavily source {source.slug} has no query, skipping")
            return []

        if not settings.TAVILY_API_KEY or settings.TAVILY_API_KEY == "TODO_BEFORE_PHASE_1":
            log.warning(f"Tavily source {source.slug} skipped: TAVILY_API_KEY not set")
            return []

        now = datetime.now(timezone.utc)
        if source.last_fetched_at:
            cadence = extra.get("cadence_minutes", 90)
            age = (now - source.last_fetched_at).total_seconds() / 60
            if age < cadence:
                log.info(f"Tavily source {source.slug} skipped: {age:.0f}m since last fetch (< {cadence}m cadence)")
                return []

        payload = {
            "api_key": settings.TAVILY_API_KEY,
            "query": query,
            "max_results": extra.get("max_results", 20),
            "search_depth": extra.get("search_depth", "basic"),
            "topic": extra.get("topic", "news"),
            "include_domains": extra.get("include_domains", []),
            "exclude_domains": extra.get("exclude_domains", []),
        }
        if payload["topic"] == "news":
            payload["days"] = extra.get("days", 1)

        response = await http.post(TAVILY_SEARCH_URL, json=payload, timeout=30.0)
        response.raise_for_status()
        result = response.json()

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

        log.info(f"Tavily source {source.slug}: {len(candidates)} candidates for query={query!r}")
        return candidates
