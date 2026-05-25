import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from slowapi import Limiter
from slowapi.util import get_remote_address

from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.web")

static_dir = Path(settings.STATIC_DIR)

app = FastAPI(title="BuzzNews API")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.get("/")
async def root():
    return RedirectResponse(url="/en/", status_code=302)


@app.get("/{lang}/")
@limiter.limit("60/minute")
async def home(lang: str, request: Request):
    if lang not in ("en", "hi"):
        return Response(status_code=404)
    index_path = static_dir / lang / "index.html"
    if not index_path.exists():
        return Response(f"Language {lang} not available", status_code=404)
    return FileResponse(index_path)


@app.get("/{lang}/article/{slug}")
@limiter.limit("60/minute")
async def article(lang: str, slug: str, request: Request):
    if lang not in ("en", "hi"):
        return Response(status_code=404)
    path = static_dir / lang / "article" / f"{slug}.html"
    if path.exists():
        return FileResponse(path)

    en_path = static_dir / "en" / "article" / f"{slug}.html"
    if lang == "hi" and en_path.exists():
        return FileResponse(en_path)
    return Response(status_code=404)


@app.get("/{lang}/category/{category}")
@limiter.limit("60/minute")
async def category(lang: str, category: str, request: Request):
    if lang not in ("en", "hi"):
        return Response(status_code=404)
    path = static_dir / lang / "category" / f"{category}.html"
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path)


@app.get("/{lang}/archive/{period}/{date}")
@limiter.limit("60/minute")
async def archive(lang: str, period: str, date: str, request: Request):
    if lang not in ("en", "hi"):
        return Response(status_code=404)
    path = static_dir / lang / "archive" / period / f"{date}.html"
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path)


@app.get("/api/search", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def search(request: Request, q: str = "", lang: str = "en"):
    """Hybrid FTS + pgvector search. Returns a full HTML results page.
    Cost-bounded by search.MAX_DAILY_EMBEDS."""
    from buzz_news.search import hybrid_search
    from buzz_news.publisher import _archive_windows, _ist_day_window
    from buzz_news.web.i18n import get_labels
    from jinja2 import Environment, FileSystemLoader
    from datetime import datetime, timezone

    if lang not in ("en", "hi"):
        return Response(status_code=404)
    query = (q or "").strip()
    if not query:
        return RedirectResponse(url=f"/{lang}/archive/today", status_code=302)

    results = await hybrid_search(query, lang=lang, limit=30)

    template_dir = Path(__file__).parent.parent / "web" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    env.globals["utc_now"] = lambda: datetime.now(timezone.utc)
    tpl = env.get_template("archive_search.html")

    labels = get_labels(lang)
    _, _, today_str, month_str = _ist_day_window()
    windows = _archive_windows("search", lang, labels, today_str, month_str)
    from urllib.parse import quote
    qenc = quote(query, safe="")
    lang_switch = {
        "en": f"/api/search?q={qenc}&lang=en",
        "hi": f"/api/search?q={qenc}&lang=hi",
    }

    html = tpl.render(
        lang=lang,
        period="search",
        page_key="archive/today",  # used by mast only as a fallback; lang_switch overrides
        date_str=today_str,
        articles=results,
        windows=windows,
        total_count=len(results),
        labels=labels,
        search_query=query,
        lang_switch=lang_switch,
        title=f"{labels.get('search', 'Search')}: {query}",
    )
    return HTMLResponse(content=html)


@app.get("/api/healthz")
async def healthz():
    return {"status": "ok", "lag_minutes": 0}


@app.get("/api/buzz/recent")
async def buzz_recent():
    return {"buzz_events": []}
