from datetime import datetime, timezone
from buzz_news.rollups import _DATE_FMT, _TOP_LIMIT


def test_date_fmt_keys():
    assert set(_DATE_FMT.keys()) == {"day", "week", "month", "year"}


def test_date_fmt_day():
    d = datetime(2026, 5, 22, tzinfo=timezone.utc)
    assert d.strftime(_DATE_FMT["day"]) == "2026-05-22"


def test_date_fmt_week():
    d = datetime(2026, 5, 18, tzinfo=timezone.utc)
    week_str = d.strftime(_DATE_FMT["week"])
    assert "2026-W" in week_str


def test_date_fmt_month():
    d = datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert d.strftime(_DATE_FMT["month"]) == "2026-05"


def test_date_fmt_year():
    d = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert d.strftime(_DATE_FMT["year"]) == "2026"


def test_top_limit_values():
    assert _TOP_LIMIT["day"] == 30
    assert _TOP_LIMIT["week"] == 50
    assert _TOP_LIMIT["month"] == 75
    assert _TOP_LIMIT["year"] == 100


def test_render_rollup_no_template():
    from buzz_news.rollups import _render_rollup

    articles = [
        {
            "id": 1,
            "slug": "test-article-1",
            "title_en": "Test Article 1",
            "title_hi": "परीक्षण लेख 1",
            "summary_en": "Summary text here",
            "summary_hi": "सारांश टेक्स्ट यहाँ",
            "hero_image_url": None,
            "hero_image_credit": None,
            "category": "technology",
            "region": "GLOBAL",
            "published_at": datetime.now(timezone.utc),
            "score": 0.85,
        }
    ]

    rendered = _render_rollup(
        lang="en",
        period="day",
        date_label="2026-05-22",
        articles=articles,
        period_label="Daily Roundup — 22 May 2026",
        category=None,
        region=None,
    )
    assert "Test Article 1" in rendered
    assert "Daily Roundup" in rendered
    assert "2026-05-22" in rendered


def test_render_rollup_with_category_region():
    from buzz_news.rollups import _render_rollup

    articles = [
        {
            "id": 2,
            "slug": "india-tech-2",
            "title_en": "India Tech News",
            "title_hi": None,
            "summary_en": "Indian tech summary",
            "summary_hi": None,
            "hero_image_url": None,
            "hero_image_credit": None,
            "category": "technology",
            "region": "IN",
            "published_at": datetime.now(timezone.utc),
            "score": 0.72,
        }
    ]

    rendered = _render_rollup(
        lang="en",
        period="week",
        date_label="2026-W20",
        articles=articles,
        period_label="Weekly Roundup — w/c 18 May 2026",
        category="technology",
        region="IN",
    )
    assert "India Tech News" in rendered
    assert "Technology" in rendered


def test_render_rollup_empty_articles():
    from buzz_news.rollups import _render_rollup

    rendered = _render_rollup(
        lang="en",
        period="month",
        date_label="2026-05",
        articles=[],
        period_label="Monthly Roundup — May 2026",
        category=None,
        region=None,
    )
    assert "Monthly Roundup" in rendered


def test_render_rollup_with_image():
    from buzz_news.rollups import _render_rollup

    articles = [
        {
            "id": 3,
            "slug": "with-image-3",
            "title_en": "Article With Image",
            "title_hi": "छवि वाला लेख",
            "summary_en": "Description here",
            "summary_hi": "विवरण यहाँ",
            "hero_image_url": "/static/images/3/hero.webp",
            "hero_image_credit": "Photo by Test on Unsplash",
            "category": "general",
            "region": "GLOBAL",
            "published_at": datetime.now(timezone.utc),
            "score": 0.9,
        }
    ]

    rendered = _render_rollup(
        lang="en",
        period="day",
        date_label="2026-05-22",
        articles=articles,
        period_label="Daily Roundup — 22 May 2026",
        category="general",
        region="GLOBAL",
    )
    assert "Article With Image" in rendered
    assert "General" in rendered


def test_backfill_rollups_calls_build_daily(monkeypatch):
    from buzz_news.rollups import backfill_rollups

    calls = []

    async def fake_build_daily(d):
        calls.append(d)

    import buzz_news.rollups
    monkeypatch.setattr(buzz_news.rollups, "build_daily", fake_build_daily)

    import asyncio
    asyncio.run(backfill_rollups(3))

    assert len(calls) == 3
    for d in calls:
        assert isinstance(d, datetime)
        assert d.hour == 0
        assert d.minute == 0
        assert d.second == 0
        assert d.microsecond == 0
