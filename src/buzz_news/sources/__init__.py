import logging
from typing import TYPE_CHECKING

from buzz_news.sources.base import RawCandidate
from buzz_news.sources.rss import RSSAdapter
from buzz_news.sources.reddit import RedditAdapter
from buzz_news.sources.hn import HNAdapter
from buzz_news.sources.gdelt import GDELTAdapter
from buzz_news.sources.tavily import TavilyAdapter
from buzz_news.sources.googlenews import GoogleNewsAdapter

if TYPE_CHECKING:
    from buzz_news.models import Source

log = logging.getLogger("buzz_news.sources")

ADAPTERS = {
    "rss": RSSAdapter(),
    "reddit": RedditAdapter(),
    "hn": HNAdapter(),
    "gdelt": GDELTAdapter(),
    "tavily": TavilyAdapter(),
    "googlenews": GoogleNewsAdapter(),
}

# Source kinds whose URLs we should NOT trafilatura — Google News
# article URLs are JS-redirected to the publisher and don't resolve
# server-side, so any fetch attempt returns Google's wrapper HTML.
# These items live as title/snippet-only signals.
SKIP_BODY_FETCH_KINDS = {"googlenews"}


async def fetch_source(source: "Source", http) -> list[RawCandidate]:
    kind = source.kind
    adapter = ADAPTERS.get(kind)
    if not adapter:
        raise ValueError(f"Unknown source kind: {kind}")
    return await adapter.fetch(source, http)
