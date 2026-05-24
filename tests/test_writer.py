

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
    assert "body must be 150–250 words" in EN_WRITER_PROMPT
    assert "No quoted phrases longer than 8 words" in EN_WRITER_PROMPT


def test_hi_prompt_has_hindi_guidance():
    from buzz_news.writer import HI_WRITER_PROMPT
    assert "हिन्दी" in HI_WRITER_PROMPT or "Hindi" in HI_WRITER_PROMPT
    assert "BBC Hindi" in HI_WRITER_PROMPT or "Wire Hindi" in HI_WRITER_PROMPT


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
