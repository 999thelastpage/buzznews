import pytest
from unittest.mock import MagicMock, AsyncMock

from buzz_news.sources.hn import HNAdapter
from buzz_news.models import Source


@pytest.fixture
def hn_source():
    src = MagicMock(spec=Source)
    src.id = 1
    src.slug = "hn_top"
    src.url = "https://hacker-news.firebaseio.com/v0/topstories.json"
    src.kind = "hn"
    src.language = "en"
    return src


@pytest.mark.asyncio
async def test_hn_adapter_fetches_stories(hn_source):
    async def side_effect(url, timeout=None):
        mock_resp = MagicMock()
        if "topstories" in url:
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value=[1, 2])
        else:
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(return_value={
                "id": 1,
                "title": "Test Story",
                "url": "https://example.com/story/1",
                "by": "user123",
                "time": 1716547200,
            })
        return mock_resp

    http = MagicMock()
    http.get = AsyncMock(side_effect=side_effect)

    adapter = HNAdapter()
    candidates = await adapter.fetch(hn_source, http)

    assert len(candidates) == 2
    assert candidates[0].title == "Test Story"
    assert candidates[0].url == "https://example.com/story/1"
    assert candidates[0].snippet == "by user123"
