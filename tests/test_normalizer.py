import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone
from buzz_news.sources.base import RawCandidate


SIMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test Article</title></head>
<body>
<article>
<p>This is the main content of the article with enough text to pass
the 200 character threshold for trafilatura extraction. The article
discusses important matters related to technology and innovation.</p>
</article>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_normalizer_uses_trafilatura_no_browser_fallback():
    from buzz_news.normalizer import normalize

    source = MagicMock()
    source.id = 1
    source.slug = "test_source"
    source.language = "en"
    source.extra = {}

    candidate = RawCandidate(
        external_id="ext1",
        url="https://example.com/article",
        title="Test Article",
        snippet="Short snippet",
        published_at=datetime.now(timezone.utc),
        language="en",
    )

    mock_resp = MagicMock()
    mock_resp.text = SIMPLE_HTML
    mock_resp.headers = {"content-type": "text/html"}

    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=mock_resp)

    with patch.dict("os.environ", {"OPENCLAW_BROWSER_FALLBACK_ENABLED": "false"}):
        result = await normalize(source, candidate, mock_http)

    assert result["body"] is not None
    assert result["language"] == "en"
    mock_http.get.assert_called_once()


@pytest.mark.asyncio
async def test_normalizer_zero_openclaw_calls_when_disabled():
    from buzz_news.normalizer import normalize

    source = MagicMock()
    source.id = 1
    source.slug = "test_source"
    source.language = "en"
    source.extra = {"js_heavy": True}

    candidate = RawCandidate(
        external_id="ext1",
        url="https://example.com/js-heavy-page",
        title="JS Heavy Page",
        snippet="Short",
        published_at=datetime.now(timezone.utc),
        language="en",
    )

    mock_resp = MagicMock()
    mock_resp.text = "<html><body>Very short</body></html>"
    mock_resp.headers = {"content-type": "text/html"}

    mock_http = MagicMock()
    mock_http.get = AsyncMock(return_value=mock_resp)

    openclaw_url_called = []

    async def mock_call_skill(path, payload):
        openclaw_url_called.append(path)
        raise Exception("OpenClaw should not be called")

    with patch.dict("os.environ", {"OPENCLAW_BROWSER_FALLBACK_ENABLED": "false"}):
        with patch("buzz_news.normalizer.call_skill", side_effect=mock_call_skill):
            result = await normalize(source, candidate, mock_http)

    assert len(openclaw_url_called) == 0
    assert result["body"] is not None
