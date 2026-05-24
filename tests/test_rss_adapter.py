import pytest
from unittest.mock import MagicMock, AsyncMock

from buzz_news.sources.rss import RSSAdapter, RateLimitError
from buzz_news.models import Source


@pytest.fixture
def rss_source():
    src = MagicMock(spec=Source)
    src.id = 1
    src.slug = "bbc_world"
    src.url = "https://feeds.bbci.co.uk/news/world/rss.xml"
    src.kind = "rss"
    src.language = "en"
    src.last_etag = None
    src.last_modified = None
    src.fail_count = 0
    return src


RSS_FEED_CONTENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>BBC News</title>
<item>
<guid>https://example.com/news/1</guid>
<link>https://example.com/news/1</link>
<title>Major earthquake in Japan</title>
<description>A 7.1 magnitude earthquake struck off the coast.</description>
<pubDate>Sat, 24 May 2026 10:00:00 GMT</pubDate>
</item>
<item>
<guid>https://example.com/news/2</guid>
<link>https://example.com/news/2</link>
<title>Tsunami warning issued</title>
<description>Authorities issued tsunami warning.</description>
<pubDate>Sat, 24 May 2026 10:30:00 GMT</pubDate>
</item>
</channel>
</rss>"""


@pytest.mark.asyncio
async def test_rss_adapter_parses_items(rss_source):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = RSS_FEED_CONTENT

    http = MagicMock()
    http.get = AsyncMock(return_value=mock_resp)

    adapter = RSSAdapter()
    candidates = await adapter.fetch(rss_source, http)

    assert len(candidates) == 2
    assert candidates[0].title == "Major earthquake in Japan"
    assert candidates[0].url == "https://example.com/news/1"
    assert "7.1 magnitude earthquake" in candidates[0].snippet
    assert candidates[0].language == "en"
    assert candidates[0].published_at is not None


@pytest.mark.asyncio
async def test_rss_adapter_304_returns_empty(rss_source):
    mock_resp = MagicMock()
    mock_resp.status_code = 304

    http = MagicMock()
    http.get = AsyncMock(return_value=mock_resp)

    adapter = RSSAdapter()
    candidates = await adapter.fetch(rss_source, http)

    assert candidates == []


@pytest.mark.asyncio
async def test_rss_adapter_rate_limit_raises(rss_source):
    mock_resp = MagicMock()
    mock_resp.status_code = 429

    http = MagicMock()
    http.get = AsyncMock(return_value=mock_resp)

    adapter = RSSAdapter()
    with pytest.raises(RateLimitError):
        await adapter.fetch(rss_source, http)
