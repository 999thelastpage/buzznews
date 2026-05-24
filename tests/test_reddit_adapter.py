import pytest
from unittest.mock import MagicMock, AsyncMock

from buzz_news.sources.reddit import RedditAdapter, RateLimitError
from buzz_news.models import Source


@pytest.fixture
def reddit_source():
    src = MagicMock(spec=Source)
    src.id = 1
    src.slug = "reddit_worldnews"
    src.url = "https://www.reddit.com/r/worldnews/top.json?t=hour&limit=25"
    src.kind = "reddit"
    src.language = "en"
    src.fail_count = 0
    return src


REDDIT_RESPONSE = {
    "data": {
        "children": [
            {
                "data": {
                    "url": "https://example.com/article/1",
                    "permalink": "/r/worldnews/comments/abc123/",
                    "title": "World leaders meet for summit",
                    "selftext": "Discussion about the global summit.",
                    "created_utc": 1716547200,
                }
            },
            {
                "data": {
                    "url": "https://example.com/article/2",
                    "permalink": "/r/worldnews/comments/def456/",
                    "title": "Climate change report released",
                    "selftext": "",
                    "created_utc": 1716543600,
                }
            },
        ]
    }
}


@pytest.mark.asyncio
async def test_reddit_adapter_parses_posts(reddit_source):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value=REDDIT_RESPONSE)

    http = MagicMock()
    http.get = AsyncMock(return_value=mock_resp)

    adapter = RedditAdapter()
    candidates = await adapter.fetch(reddit_source, http)

    assert len(candidates) == 2
    assert candidates[0].title == "World leaders meet for summit"
    assert candidates[0].url == "https://example.com/article/1"
    assert candidates[0].snippet == "Discussion about the global summit."
    assert candidates[1].url == "https://example.com/article/2"
    assert candidates[1].snippet is None


@pytest.mark.asyncio
async def test_reddit_adapter_rate_limit_raises(reddit_source):
    mock_resp = MagicMock()
    mock_resp.status_code = 429

    http = MagicMock()
    http.get = AsyncMock(return_value=mock_resp)

    adapter = RedditAdapter()
    with pytest.raises(RateLimitError):
        await adapter.fetch(reddit_source, http)


@pytest.mark.asyncio
async def test_reddit_adapter_403_raises(reddit_source):
    mock_resp = MagicMock()
    mock_resp.status_code = 403

    http = MagicMock()
    http.get = AsyncMock(return_value=mock_resp)

    adapter = RedditAdapter()
    with pytest.raises(RateLimitError):
        await adapter.fetch(reddit_source, http)
