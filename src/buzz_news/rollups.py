import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.models import Article, Cluster, Rollup
from buzz_news.publisher import _archive_windows, _ist_day_window, _render, _get_labels, IST

settings = get_settings()
log = logging.getLogger("buzz_news.rollups")

_MONTH_TOP_LIMIT = 500


def _ist_month_window(year: int, month: int) -> tuple[datetime, datetime, str]:
    """Return (start_utc, end_utc, month_str) for the IST calendar month."""
    start_ist = datetime(year, month, 1, 0, 0, 0, tzinfo=IST)
    if month == 12:
        end_ist = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=IST)
    else:
        end_ist = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=IST)
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc), start_ist.strftime("%Y-%m")


async def build_monthly(year: int, month: int) -> None:
    """Render <STATIC_DIR>/{en,hi}/archive/month/{YYYY-MM}.html with the month's
    articles sorted newest-first, capped at _MONTH_TOP_LIMIT.

    - IST calendar boundaries (matches editorial week)
    - No verifier_passed filter (matches today + home behavior)
    - Single "all" file per month (the per-category-overwrite bug is gone)
    """
    start_utc, end_utc, month_str = _ist_month_window(year, month)
    garbage_phrases = ("Unavailable", "Access Restrictions", "Inaccessible")

    async with async_session_factory() as session:
        result = await session.execute(
            select(
                Article.id,
                Article.slug,
                Article.title_en,
                Article.title_hi,
                Cluster.category.label("category"),
                Article.region,
                Article.hero_image_url,
                Article.published_at,
                Cluster.current_score,
                Cluster.source_count,
                Article.cluster_id,
            )
            .join(Cluster, Article.cluster_id == Cluster.id)
            .where(Article.published_at >= start_utc)
            .where(Article.published_at < end_utc)
            .where(*[~Article.title_en.contains(p) for p in garbage_phrases])
            .order_by(Article.published_at.desc())
            .limit(_MONTH_TOP_LIMIT)
        )
        rows = result.fetchall()

    if not rows:
        log.warning(f"build_monthly({year}-{month:02d}): no articles in IST month window")
        return

    article_ids = [int(r.id) for r in rows]
    await _upsert_rollup("month", start_utc, end_utc, article_ids)

    static_dir = Path(settings.STATIC_DIR)
    _, _, today_str, current_month_str = _ist_day_window()
    month_label_dt = datetime(year, month, 1, tzinfo=IST)

    for lang in ("en", "hi"):
        articles = [
            {
                "id": r.id,
                "slug": r.slug,
                "title_en": r.title_en,
                "title_hi": r.title_hi,
                "category": r.category or "general",
                "region": r.region,
                "hero_image_url": r.hero_image_url,
                "published_at": r.published_at,
                "source_count": r.source_count or 1,
                "score": float(r.current_score or 0),
            }
            for r in rows
            if not (lang == "hi" and not r.title_hi)
        ]
        if not articles:
            continue

        labels = _get_labels(lang)
        period_label = (
            f"{labels.get('this_month', 'This Month')} — {month_label_dt.strftime('%B %Y')}"
            if lang == "en"
            else f"{labels.get('this_month', 'इस महीने')} — {month_label_dt.strftime('%B %Y')}"
        )
        windows = _archive_windows("month", lang, labels, today_str, current_month_str)

        html = _render(
            "archive.html",
            lang=lang,
            period="month",
            period_label=period_label,
            date_str=month_str,
            page_key=f"archive/month/{month_str}",
            articles=articles,
            windows=windows,
            total_count=len(articles),
            labels=labels,
            og_description=f"Monthly archive — {len(articles)} stories from {month_label_dt.strftime('%B %Y')}",
            search_query="",
        )
        out_path = static_dir / lang / "archive" / "month" / f"{month_str}.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        log.info(f"Rendered monthly archive: {out_path} ({len(articles)} articles)")

    await _regenerate_sitemap()


async def _upsert_rollup(
    period: str,
    start: datetime,
    end: datetime,
    article_ids: list[int],
) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Rollup).where(
                Rollup.period == period,
                Rollup.start_at == start,
                Rollup.end_at == end,
                Rollup.category.is_(None),
                Rollup.region.is_(None),
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.article_ids = article_ids
        else:
            session.add(
                Rollup(
                    period=period,
                    start_at=start,
                    end_at=end,
                    article_ids=article_ids,
                    category=None,
                    region=None,
                )
            )
        await session.commit()


async def _regenerate_sitemap() -> None:
    base_url = settings.SITE_BASE_URL or "https://example.com"
    static_dir = Path(settings.STATIC_DIR)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Article).order_by(Article.published_at.desc()).limit(1000)
        )
        articles = list(result.scalars().all())

    urls = [f"{base_url}/en/archive/today", f"{base_url}/hi/archive/today"]
    _, _, _, current_month = _ist_day_window()
    urls.append(f"{base_url}/en/archive/month/{current_month}")
    urls.append(f"{base_url}/hi/archive/month/{current_month}")
    for art in articles:
        urls.append(f"{base_url}/en/article/{art.slug}")
        if art.title_hi:
            urls.append(f"{base_url}/hi/article/{art.slug}")

    sitemap_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url in urls:
        sitemap_lines.append(f"  <url><loc>{url}</loc></url>")
    sitemap_lines.append("</urlset>")

    out_path = static_dir / "sitemap.xml"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sitemap_lines))
    log.info(f"Regenerated sitemap with {len(urls)} URLs")
