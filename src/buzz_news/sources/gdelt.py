import logging
from datetime import datetime

import httpx

from buzz_news.sources.base import RawCandidate
from buzz_news.models import Source

log = logging.getLogger("buzz_news.sources.gdelt")


class GDELTAdapter:
    async def fetch(self, source: Source, http: httpx.AsyncClient) -> list[RawCandidate]:
        try:
            resp = await http.get(source.url, timeout=30.0)
        except Exception:
            raise

        if resp.status_code >= 400:
            raise Exception(f"GDELT fetch failed {resp.status_code}: {source.url}")

        data = resp.json()
        articles = data.get("articles", [])
        candidates = []
        for item in articles:
            url = item.get("url") or item.get("link")
            if not url:
                continue
            seendate = item.get("seendate", "")
            published = None
            if seendate:
                try:
                    published = datetime.fromisoformat(seendate.replace("Z", "+00:00"))
                except Exception:
                    pass
            candidates.append(RawCandidate(
                external_id=url,
                url=url,
                title=item.get("title", "No title") or "No title",
                snippet=item.get("snippet") or item.get("socialimage") or None,
                published_at=published,
                language=source.language,
            ))

        return candidates
