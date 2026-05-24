import yaml
from pathlib import Path
from functools import lru_cache


DEFAULT_LANG = "en"
INDIAN_COUNTRY_CODES = {"IN"}


@lru_cache
def get_labels(lang: str) -> dict:
    if lang not in ("en", "hi"):
        lang = DEFAULT_LANG
    path = Path(__file__).parent / f"{lang}.yaml"
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def detect_language(request) -> str:
    cookie_lang = request.cookies.get("lang")
    if cookie_lang in ("en", "hi"):
        return cookie_lang

    cf_country = request.headers.get("CF-IPCountry", "")
    if cf_country in INDIAN_COUNTRY_CODES:
        return "hi"

    accept = request.headers.get("Accept-Language", "")
    if "hi" in accept.lower():
        return "hi"
    return DEFAULT_LANG
