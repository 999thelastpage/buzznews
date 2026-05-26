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


def test_paywall_block_detected_for_ndtv_pattern():
    from buzz_news.normalizer import _looks_paywall_block
    # Real NDTV block page text trafilatura extracted (282 chars).
    text = (
        "Access Denied\n"
        "You don't have permission to access "
        '"http://www.ndtv.com/india-news/some-article-11551555" on this server.\n'
        "Reference #18.16eb1cb8.1779819445.89bd5903\n"
        "https://errors.edgesuite.net/18.16eb1cb8.1779819445.89bd5903"
    )
    assert _looks_paywall_block(text)


def test_paywall_block_detected_for_subscription_patterns():
    from buzz_news.normalizer import _looks_paywall_block
    assert _looks_paywall_block("Subscribe to continue reading this article.")
    assert _looks_paywall_block("Sign in to read the full story")
    assert _looks_paywall_block("This content is for subscribers only. Become a member to read.")
    assert _looks_paywall_block("Checking your browser before accessing...")


def test_paywall_block_does_not_flag_real_articles():
    from buzz_news.normalizer import _looks_paywall_block
    # Real article body that happens to mention "sign in" in passing
    text = (
        "The company announced Tuesday that customers will need to sign in "
        "to their accounts more often, as part of a security update rolling "
        "out this month. The change affects all 12 million users, executives "
        "said in a statement to investors. Authorities are reviewing the "
        "implementation timeline ahead of the December deadline." * 3
    )
    assert not _looks_paywall_block(text)


def test_paywall_block_handles_empty_and_none():
    from buzz_news.normalizer import _looks_paywall_block
    assert not _looks_paywall_block(None)
    assert not _looks_paywall_block("")
