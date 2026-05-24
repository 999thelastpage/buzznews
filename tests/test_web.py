from buzz_news.web.i18n import get_labels, detect_language


def test_get_labels_en():
    labels = get_labels("en")
    assert labels["site_name"] == "BuzzNews"
    assert "trending_now" in labels


def test_get_labels_hi():
    labels = get_labels("hi")
    assert labels["site_name"] == "बज़न्यूज़"
    assert "trending_now" in labels


def test_get_labels_invalid_defaults_to_en():
    labels = get_labels("fr")
    assert labels.get("site_name") == "BuzzNews"


def test_detect_language_cookie_en():
    class MockRequest:
        def __init__(self):
            self.cookies = {"lang": "en"}
            self.headers = {}
    assert detect_language(MockRequest()) == "en"


def test_detect_language_cookie_hi():
    class MockRequest:
        def __init__(self):
            self.cookies = {"lang": "hi"}
            self.headers = {}
    assert detect_language(MockRequest()) == "hi"


def test_detect_language_cf_ipcountry_in():
    class MockRequest:
        def __init__(self):
            self.cookies = {}
            self.headers = {"CF-IPCountry": "IN"}
    assert detect_language(MockRequest()) == "hi"


def test_detect_language_accept_language_hi():
    class MockRequest:
        def __init__(self):
            self.cookies = {}
            self.headers = {"Accept-Language": "hi,en;q=0.9"}
    assert detect_language(MockRequest()) == "hi"


def test_detect_language_default_en():
    class MockRequest:
        def __init__(self):
            self.cookies = {}
            self.headers = {}
    assert detect_language(MockRequest()) == "en"
