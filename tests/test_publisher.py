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
