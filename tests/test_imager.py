from buzz_news.imager import (
    CATEGORY_QUERIES,
    _build_query,
    _extract_keywords,
    _is_relevant,
    _tokens,
)


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


def test_build_query_prefers_writer_image_query():
    query, terms = _build_query(
        "cricket batsman in stadium", "sports",
        "Gujarat Titans win toss against RCB", "IPL qualifier...",
    )
    assert query == "cricket batsman in stadium"
    # guard terms come from the writer query plus the category, never the
    # misleading proper nouns in the title
    assert "cricket" in terms and "stadium" in terms
    assert "titans" not in terms and "gujarat" not in terms


def test_build_query_falls_back_to_category():
    query, terms = _build_query(None, "sports", "Some headline", "body text")
    assert query == CATEGORY_QUERIES["sports"]
    assert "stadium" in terms


def test_build_query_falls_back_to_keywords():
    # no writer query and an unknown/None category → legacy frequency keywords
    query, terms = _build_query(None, None, "Earthquake hits Japan", "")
    assert "earthquake" in query
    assert "earthquake" in terms


def test_category_queries_cover_all_writer_categories():
    # every category the writer can emit must have a safe fallback query
    from buzz_news.writer import VALID_CATEGORIES
    for cat in VALID_CATEGORIES:
        assert cat in CATEGORY_QUERIES


def test_is_relevant_overlap_passes():
    assert _is_relevant("a cricket match in the stadium", {"cricket", "stadium"})


def test_is_relevant_no_overlap_rejects():
    # the football-story-gets-a-bowling-photo case
    assert not _is_relevant("man holding bowling ball at alley", {"soccer", "football", "field"})


def test_is_relevant_accepts_when_unjudgeable():
    # no query terms, or candidate has no describable text → can't disprove
    assert _is_relevant("anything", set())
    assert _is_relevant(None, {"cricket"})
    assert _is_relevant("", {"cricket"})


def test_tokens_drops_stopwords_and_short():
    assert _tokens("The big cat") == {"big", "cat"}
    assert _tokens(None) == set()
