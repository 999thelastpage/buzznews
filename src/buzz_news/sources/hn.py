import logging
from datetime import datetime, timezone

import httpx

from buzz_news.sources.base import RawCandidate
from buzz_news.models import Source

log = logging.getLogger("buzz_news.sources.hn")


class HNAdapter:
    async def fetch(self, source: Source, http: httpx.AsyncClient) -> list[RawCandidate]:
        try:
            resp = await http.get(source.url, timeout=15.0)
        except Exception:
            raise

        if resp.status_code >= 400:
            raise Exception(f"HN fetch failed {resp.status_code}: {source.url}")

        story_ids: list[int] = resp.json()
        top_ids = story_ids[:25]

        candidates = []
        for sid in top_ids:
            try:
                story_resp = await http.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                    timeout=10.0,
                )
                if story_resp.status_code >= 400:
                    continue
                story = story_resp.json()
                if not story or story.get("deleted") or story.get("dead"):
                    continue
                url = story.get("url") or f"https://news.ycombinator.com/item?id={sid}"
                by = story.get("by", "")
                title = story.get("title", "No title")
                time_val = story.get("time", 0)
                published = datetime.fromtimestamp(time_val, tz=timezone.utc) if time_val else None
                candidates.append(RawCandidate(
                    external_id=str(sid),
                    url=url,
                    title=title,
                    snippet=f"by {by}" if by else None,
                    published_at=published,
                    language=source.language,
                ))
            except Exception as e:
                log.warning(f"Failed to fetch HN story {sid}: {e}")
                continue

        return candidates
