import logging
from typing import TYPE_CHECKING

from buzz_news.sources.base import RawCandidate
from buzz_news.sources.rss import RSSAdapter
from buzz_news.sources.reddit import RedditAdapter
from buzz_news.sources.hn import HNAdapter
from buzz_news.sources.gdelt import GDELTAdapter
from buzz_news.sources.tavily import TavilyAdapter

if TYPE_CHECKING:
    from buzz_news.models import Source

log = logging.getLogger("buzz_news.sources")

ADAPTERS = {
    "rss": RSSAdapter(),
    "reddit": RedditAdapter(),
    "hn": HNAdapter(),
    "gdelt": GDELTAdapter(),
    "tavily": TavilyAdapter(),
}


async def fetch_source(source: "Source", http) -> list[RawCandidate]:
    kind = source.kind
    adapter = ADAPTERS.get(kind)
    if not adapter:
        raise ValueError(f"Unknown source kind: {kind}")
    return await adapter.fetch(source, http)
