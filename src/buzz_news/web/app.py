import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
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


@app.get("/api/healthz")
async def healthz():
    return {"status": "ok", "lag_minutes": 0}


@app.get("/api/buzz/recent")
async def buzz_recent():
    return {"buzz_events": []}
