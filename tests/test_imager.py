from buzz_news.imager import _extract_keywords


def test_extract_keywords_basic():
    title = "Major earthquake hits Japan with tsunami warning"
    keywords = _extract_keywords(title)
    assert "earthquake" in keywords
    assert "japan" in keywords
    assert "tsunami" in keywords


def test_extract_keywords_filters_stopwords():
    title = "The quick brown fox jumps over the lazy elephant"
    keywords = _extract_keywords(title, max_keywords=10)
    assert "the" not in keywords
    assert "quick" in keywords
    assert "brown" in keywords
    assert "elephant" in keywords
    assert "fox" not in keywords


def test_extract_keywords_max_count():
    title = " and ".join(["word"] * 20)
    keywords = _extract_keywords(title, max_keywords=5)
    assert len(keywords) <= 5


def test_extract_keywords_with_body():
    title = "Earthquake in Japan"
    body = "A powerful earthquake struck Japan's coastal region"
    keywords = _extract_keywords(title, body)
    assert "earthquake" in keywords
    assert "japan" in keywords
    assert "powerful" in keywords
