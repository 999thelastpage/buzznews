"""Google News RSS adapter.

Google News aggregates and de-duplicates headlines across hundreds of
publishers and exposes per-country / per-topic RSS feeds. We use it as a
coverage-expanding meta-source: titles arrive with publisher attribution
from `entry.source.title`, dedup-grouping is done on Google's side.

Important quirks:

- The `link` returned by Google News is a `news.google.com/rss/articles/...`
  redirect URL. Following it server-side does NOT resolve to the
  publisher's article — Google's resolution is JS-based / consent-gated.
  Decoding the base64 payload is possible but fragile. We therefore mark
  Google News items as `skip_body_fetch` so the normalizer never tries
  to trafilatura them — they live as title-only signals.
- Titles include the publisher suffix (" - The Hindu", " | BBC"). We
  strip the trailing publisher hint so the title reads cleanly in the UI;
  the publisher name from `entry.source.title` is what we display.
- Items are short on content but high on coverage. The thin-source gate
  in publisher.publish_top_n prevents Google-News-only clusters from
  publishing as articles; their value is strengthening clusters that
  also have rich sources (Hindu / BBC / HT).
"""
import logging
import re
import time
from datetime import datetime, timezone

import feedparser
import httpx

from buzz_news.sources.base import RawCandidate
from buzz_news.models import Source

log = logging.getLogger("buzz_news.sources.googlenews")

# Strip " - Publisher" or " | Publisher" trailing from Google News titles.
# Google News always appends the source name with one of these separators.
_TITLE_PUBLISHER_TAIL = re.compile(r"\s+[-|–—]\s+[^\-|–—]{1,80}$")


def _clean_title(raw_title: str) -> str:
    if not raw_title:
        return raw_title
    return _TITLE_PUBLISHER_TAIL.sub("", raw_title).strip()


class GoogleNewsAdapter:
    async def fetch(self, source: Source, http: httpx.AsyncClient) -> list[RawCandidate]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; BuzzNewsBot/1.0)"}
        resp = await http.get(source.url, headers=headers, timeout=15.0, follow_redirects=True)
        if resp.status_code >= 400:
            raise Exception(f"Google News fetch failed {resp.status_code}: {source.url}")

        feed = feedparser.parse(resp.content)
        candidates = []
        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue

            published = None
            if entry.get("published_parsed"):
                try:
                    t = time.mktime(entry.published_parsed)
                    published = datetime.fromtimestamp(t, tz=timezone.utc)
                except Exception:
                    pass

            title = _clean_title(entry.get("title") or "")
            if not title:
                continue

            # Keep the original title as snippet so the writer has *some*
            # content to work with when this item lands in a cluster. The
            # body field is left None — normalizer skips fetch for this
            # source kind.
            candidates.append(RawCandidate(
                external_id=entry.get("id") or link,
                url=link,
                title=title,
                snippet=entry.get("title") or None,
                published_at=published,
                language=source.language,
            ))

        log.info(f"Google News source {source.slug}: {len(candidates)} candidates")
        return candidates
