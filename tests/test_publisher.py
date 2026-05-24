

def test_slugify_function():
    from buzz_news.publisher import _slugify
    slug = _slugify("Major Earthquake Hits Japan", 123)
    assert "major-earthquake-hits-japan" in slug
    assert "123" in slug


def test_render_home_without_template():
    from buzz_news.publisher import _render_home
    articles = [{"title": "Test", "slug": "test-1", "hero_image_url": None}]
    result = _render_home(articles, "en")
    assert "Test" in result or result == "Home page"
