

def test_build_sources_block():
    from buzz_news.writer import _build_sources_block
    items = [
        {"title": "Earthquake hits Japan", "body": "A 7.1 magnitude earthquake...", "snippet": None, "url": "https://example.com/1", "published_at": None},
        {"title": "Tsunami warning issued", "body": None, "snippet": "A tsunami warning was issued...", "url": "https://example.com/2", "published_at": None},
    ]
    sources = [
        {"name": "BBC", "authority": 0.9, "url": "https://example.com/1"},
        {"name": "Reuters", "authority": 0.95, "url": "https://example.com/2"},
    ]
    block = _build_sources_block(items, sources)
    assert "BBC" in block
    assert "Reuters" in block
    assert "Earthquake hits Japan" in block
    assert "Tsunami warning issued" in block
    assert "https://example.com/1" in block


def test_writer_prompt_has_strict_json_requirement():
    from buzz_news.writer import EN_WRITER_PROMPT
    assert "valid JSON" in EN_WRITER_PROMPT
    assert "280–360 words" in EN_WRITER_PROMPT
    assert "No quoted phrases longer than 8 words" in EN_WRITER_PROMPT
    # Do NOT name source outlets in the prose — sources are listed separately.
    assert "DO NOT name source outlets" in EN_WRITER_PROMPT


def test_hi_prompt_has_hindi_guidance():
    from buzz_news.writer import HI_WRITER_PROMPT
    assert "हिन्दी" in HI_WRITER_PROMPT or "Hindi" in HI_WRITER_PROMPT
    assert "BBC हिंदी" in HI_WRITER_PROMPT or "द वायर हिंदी" in HI_WRITER_PROMPT


def test_meta_error_guard_catches_self_reference():
    from buzz_news.writer import _looks_meta_error
    # Real Gemini hallucination we saw on cluster 1387 (Lucknow, 2026-05-27)
    body = (
        "The automated news generation process was unable to produce a "
        "comprehensive article based on the dispatches provided. Analysis "
        "of the source material revealed a critical lack of accessible "
        "content, preventing the synthesis of factual information..."
    )
    assert _looks_meta_error("Some title", body)


def test_meta_error_guard_accepts_normal_news():
    from buzz_news.writer import _looks_meta_error
    body = (
        "Australia has recorded its first diphtheria death in almost a "
        "decade as the country grapples with its worst outbreak of the "
        "vaccine-preventable disease in decades. The man died in April "
        "at Royal Darwin Hospital."
    )
    assert not _looks_meta_error("Australia diphtheria death", body)


def test_meta_error_guard_rejects_empty_or_tiny():
    from buzz_news.writer import _looks_meta_error
    assert _looks_meta_error("", "")
    assert _looks_meta_error("Title", "")
    assert _looks_meta_error("Title", "Tiny body.")  # under 40 chars


def test_writer_prompt_has_refusal_sentinel():
    from buzz_news.writer import EN_WRITER_PROMPT, INSUFFICIENT_SOURCES_SENTINEL
    assert INSUFFICIENT_SOURCES_SENTINEL in EN_WRITER_PROMPT


def test_article_draft_dataclass():
    from buzz_news.writer import ArticleDraft
    draft = ArticleDraft(
        title_en="Test Title",
        body_en="Test body content",
        title_hi="परीक्षण शीर्षक",
        body_hi="परीक्षण सामग्री",
    )
    assert draft.title_en == "Test Title"
    assert draft.body_hi == "परीक्षण सामग्री"
    assert draft.title_hi == "परीक्षण शीर्षक"
    # category is optional and defaults to None (caller falls back to the
    # cluster's catalog category when the writer didn't classify).
    assert draft.category is None


def test_validate_category_accepts_all_enum_values():
    from buzz_news.writer import VALID_CATEGORIES, _validate_category
    for cat in VALID_CATEGORIES:
        assert _validate_category(cat) == cat
    # case-insensitive, whitespace-tolerant
    assert _validate_category("  Sports ") == "sports"
    assert _validate_category("TECH") == "tech"


def test_validate_category_rejects_unknown():
    from buzz_news.writer import _validate_category
    assert _validate_category("entertainment") is None
    assert _validate_category("science") is None
    assert _validate_category("world news") is None
    assert _validate_category("") is None
    assert _validate_category(None) is None


def test_en_prompt_requests_category():
    from buzz_news.writer import EN_WRITER_PROMPT, VALID_CATEGORIES
    # output schema now includes the category field
    assert '"category": string' in EN_WRITER_PROMPT
    assert "CATEGORY:" in EN_WRITER_PROMPT
    # every enum value the templates support is offered to the model
    for cat in VALID_CATEGORIES:
        assert f'"{cat}"' in EN_WRITER_PROMPT


def test_hi_prompt_does_not_request_category():
    # HI shares the cluster's category (taken from the EN pass); asking twice
    # risks the two languages disagreeing.
    from buzz_news.writer import HI_WRITER_PROMPT
    assert "CATEGORY:" not in HI_WRITER_PROMPT


def test_article_draft_image_query_defaults_none():
    from buzz_news.writer import ArticleDraft
    draft = ArticleDraft(
        title_en="T", body_en="B", title_hi=None, body_hi=None,
    )
    assert draft.image_query is None


def test_validate_image_query_normalizes():
    from buzz_news.writer import _validate_image_query
    assert _validate_image_query("  Cricket  Batsman  In Stadium ") == "cricket batsman in stadium"
    assert _validate_image_query("Soccer Players On Field") == "soccer players on field"


def test_validate_image_query_rejects_empty():
    from buzz_news.writer import _validate_image_query
    assert _validate_image_query(None) is None
    assert _validate_image_query("") is None
    assert _validate_image_query("   ") is None


def test_validate_image_query_caps_length():
    from buzz_news.writer import _validate_image_query
    out = _validate_image_query(" ".join(["word"] * 20))
    assert len(out.split()) <= 8
    assert len(out) <= 80


def test_en_prompt_requests_image_query():
    from buzz_news.writer import EN_WRITER_PROMPT
    assert '"image_query": string' in EN_WRITER_PROMPT
    assert "IMAGE_QUERY:" in EN_WRITER_PROMPT
    # the prompt must steer the model away from proper nouns in the query
    assert "proper nouns" in EN_WRITER_PROMPT


def test_hi_prompt_does_not_request_image_query():
    from buzz_news.writer import HI_WRITER_PROMPT
    assert "IMAGE_QUERY:" not in HI_WRITER_PROMPT
    assert "image_query" not in HI_WRITER_PROMPT
