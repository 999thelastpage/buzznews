from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_home_template_exists():
    template_path = Path(__file__).parent.parent / "src" / "buzz_news" / "web" / "templates" / "home.html"
    assert template_path.exists(), "home.html template should exist"


def test_article_template_exists():
    template_path = Path(__file__).parent.parent / "src" / "buzz_news" / "web" / "templates" / "article.html"
    assert template_path.exists(), "article.html template should exist"


def test_base_template_exists():
    template_path = Path(__file__).parent.parent / "src" / "buzz_news" / "web" / "templates" / "base.html"
    assert template_path.exists(), "base.html template should exist"


def test_render_home_produces_tiles():
    from buzz_news.publisher import _render_home

    articles = [
        {
            "id": 1,
            "slug": "alpha-1",
            "title_en": "Alpha headline",
            "title_hi": None,
            "category": "international",
            "region": "GLOBAL",
            "hero_image_url": None,
            "published_at": datetime.now(timezone.utc),
            "score": 0.82,
            "source_count": 5,
            "source_names": ["Reuters", "BBC", "AFP"],
            "sources": [
                {"name": "Reuters", "url": "https://reuters.com", "title": "Reuters Story"},
                {"name": "BBC", "url": "https://bbc.com", "title": "BBC Story"},
                {"name": "AFP", "url": "https://afp.com", "title": "AFP Story"},
            ],
            "trending_data": [
                {"score": 0.5, "ts": "2026-05-25T10:00:00+00:00"},
                {"score": 0.82, "ts": "2026-05-25T12:00:00+00:00"},
            ],
            "why_it_matters": "Why Alpha matters.",
        },
        {
            "id": 2,
            "slug": "beta-2",
            "title_en": "Beta headline",
            "title_hi": None,
            "category": "technology",
            "region": "GLOBAL",
            "hero_image_url": None,
            "published_at": datetime.now(timezone.utc),
            "score": 0.5,
            "source_count": 3,
            "source_names": ["The Verge", "Wired"],
            "sources": [
                {"name": "The Verge", "url": "https://theverge.com", "title": "Verge Story"},
                {"name": "Wired", "url": "https://wired.com", "title": "Wired Story"},
            ],
            "trending_data": [
                {"score": 0.3, "ts": "2026-05-25T10:00:00+00:00"},
                {"score": 0.5, "ts": "2026-05-25T12:00:00+00:00"},
            ],
            "why_it_matters": "Why Beta matters.",
        },
    ]
    html = _render_home(
        articles=articles,
        lang="en",
        cluster_count=10,
        published_count=2,
        date_str="25 May 2026",
        month_str="2026-05",
    )
    assert "<!doctype html" in html.lower()
    assert "col-span-12" in html
    assert "tile-lg" in html
    assert "col-span-12 md:col-span-6 lg:col-span-4" in html
    assert "Alpha headline" in html
    assert "Reuters" in html


def test_should_refresh_article_requires_newer_raw_content_and_debounce():
    from buzz_news.publisher import _should_refresh_article

    now = datetime(2026, 5, 28, 12, tzinfo=timezone.utc)
    updated = now - timedelta(hours=3)

    assert _should_refresh_article(updated, updated + timedelta(minutes=5), now) is True
    assert _should_refresh_article(updated, updated, now) is False
    assert _should_refresh_article(now - timedelta(hours=1), now, now) is False
    assert _should_refresh_article(None, now, now) is False
    assert _should_refresh_article(updated, None, now) is False


def test_should_show_updated_at_has_grace_window():
    from buzz_news.publisher import _should_show_updated_at

    published = datetime(2026, 5, 28, 10, tzinfo=timezone.utc)
    assert _should_show_updated_at(published, published + timedelta(minutes=6)) is True
    assert _should_show_updated_at(published, published + timedelta(minutes=5)) is False
    assert _should_show_updated_at(published, published) is False


def test_render_hindi_article_falls_back_to_english_without_404_gap():
    from buzz_news.publisher import _render_hindi_article_or_fallback

    published = datetime(2026, 5, 28, 10, tzinfo=timezone.utc)
    html = _render_hindi_article_or_fallback(
        1,
        "English headline",
        "English first paragraph.\n\nEnglish second paragraph.",
        None,
        None,
        "general",
        "GLOBAL",
        None,
        None,
        [],
        False,
        None,
        [],
        [],
        published,
        slug="english-headline-1",
    )

    assert 'lang="hi"' in html
    assert "यह लेख हिन्दी में उपलब्ध नहीं है" in html
    assert "English first paragraph." in html
    assert "/hi/article/english-headline-1" in html


def test_render_article_shows_updated_timestamp_only_for_material_refresh():
    from buzz_news.publisher import _render_article

    published = datetime(2026, 5, 28, 10, tzinfo=timezone.utc)
    html = _render_article(
        1, "en", "Updated story", "First paragraph.", "general", "GLOBAL",
        None, None, [], False, None, [], [], published, slug="updated-story-1",
        updated_at=published + timedelta(hours=3),
    )
    assert "Updated" in html
    assert html.count('class="article__kicker-time"') == 2

    html = _render_article(
        1, "en", "Fresh story", "First paragraph.", "general", "GLOBAL",
        None, None, [], False, None, [], [], published, slug="fresh-story-1",
        updated_at=published + timedelta(minutes=1),
    )
    assert "Updated" not in html
    assert html.count('class="article__kicker-time"') == 1


def test_deepseek_candidate_uses_source_or_authority_signal():
    from buzz_news.publisher import _is_deepseek_candidate

    class C:
        distinct_sources = 2
        authority_sum = 0
        source_count = 0

    assert _is_deepseek_candidate(C()) is True

    class HighAuthority:
        distinct_sources = 1
        authority_sum = 0.8
        source_count = 1

    assert _is_deepseek_candidate(HighAuthority()) is True

    class Weak:
        distinct_sources = 1
        authority_sum = 0.5
        source_count = 1

    assert _is_deepseek_candidate(Weak()) is False


def test_deepseek_allowed_so_far_is_paced(monkeypatch):
    from buzz_news import publisher

    monkeypatch.setattr(publisher.settings, "DEEPSEEK_DAILY_ARTICLE_CAP", 60)
    now = datetime(2026, 5, 28, 18, 30, tzinfo=timezone.utc)  # midnight IST
    assert publisher._deepseek_allowed_so_far(now) == 3

    noon_ist = datetime(2026, 5, 29, 6, 30, tzinfo=timezone.utc)
    assert publisher._deepseek_allowed_so_far(noon_ist) == 33
