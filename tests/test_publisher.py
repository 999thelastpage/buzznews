from datetime import datetime, timezone
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
            "trending_data": [0.5, 0.82],
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
            "trending_data": [0.3, 0.5],
            "why_it_matters": "Why Beta matters.",
        },
    ]
    html = _render_home(
        articles=articles,
        lang="en",
        cluster_count=10,
        published_count=2,
        date_str="25 May 2026",
        archive_str="2026-05-24",
    )
    assert "<!doctype html" in html.lower()
    assert "col-span-12" in html
    assert "tile-lg" in html
    assert "col-span-12 md:col-span-6 lg:col-span-4" in html
    assert "Alpha headline" in html
    assert "Reuters" in html
