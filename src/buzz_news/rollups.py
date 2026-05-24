import logging
from datetime import datetime, timezone, timedelta
from math import sqrt
from pathlib import Path
from typing import Any

from sqlalchemy import select

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.models import Article, Cluster, Rollup

settings = get_settings()
log = logging.getLogger("buzz_news.rollups")

_TOP_LIMIT = {"day": 30, "week": 50, "month": 75, "year": 100}
_DATE_FMT = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
    "year": "%Y",
}


def _render_rollup(
    lang: str,
    period: str,
    date_label: str,
    articles: list[dict[str, Any]],
    period_label: str,
    category: str | None,
    region: str | None,
) -> str:
    from jinja2 import Environment, FileSystemLoader
    from buzz_news.web.i18n import get_labels
    template_dir = Path(__file__).parent / "web" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    env.globals["utc_now"] = lambda: datetime.now(timezone.utc)
    try:
        tpl = env.get_template("archive.html")
    except Exception:
        tpl = env.from_string("{{ period_label }}: {{ articles|length }} articles")

    labels = get_labels(lang)

    return tpl.render(
        lang=lang,
        period=period,
        date_str=date_label,
        period_label=period_label,
        articles=articles,
        labels=labels,
        total_count=len(articles),
        og_description=f"Top articles for {period_label}",
    )


async def _get_articles_in_window(
    start: datetime,
    end: datetime,
    limit: int,
    category: str | None = None,
    region: str | None = None,
) -> list[dict[str, Any]]:
    async with async_session_factory() as session:
        query = (
            select(Article, Cluster.current_score)
            .join(Cluster, Article.cluster_id == Cluster.id)
            .where(Article.published_at >= start)
            .where(Article.published_at < end)
            .where(Article.verifier_passed)
            .order_by(Cluster.current_score.desc())
        )
        if category:
            query = query.where(Article.category == category)
        if region:
            query = query.where(Article.region == region)

        result = await session.execute(query.limit(limit))
        rows = result.fetchall()

        return [
            {
                "id": art.id,
                "slug": art.slug,
                "title_en": art.title_en,
                "title_hi": art.title_hi,
                "summary_en": art.summary_en,
                "summary_hi": art.summary_hi,
                "hero_image_url": art.hero_image_url,
                "hero_image_credit": art.hero_image_credit,
                "category": art.category,
                "region": art.region,
                "published_at": art.published_at,
                "score": float(score) if score else 0.0,
            }
            for art, score in rows
        ]


async def _upsert_rollup(
    period: str,
    start: datetime,
    end: datetime,
    article_ids: list[int],
    category: str | None,
    region: str | None,
) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Rollup).where(
                Rollup.period == period,
                Rollup.start_at == start,
                Rollup.end_at == end,
                Rollup.category == category,
                Rollup.region == region,
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
                    category=category,
                    region=region,
                )
            )
        await session.commit()


def _render_and_save_rollup(
    lang: str,
    period: str,
    date_label: str,
    articles: list[dict[str, Any]],
    period_label: str,
    category: str | None,
    region: str | None,
) -> None:
    rendered = _render_rollup(
        lang, period, date_label, articles, period_label, category, region
    )
    static_dir = Path(settings.STATIC_DIR)
    out_path = static_dir / lang / "archive" / period / f"{date_label}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(rendered)
    log.info(f"Rendered rollup {period}/{date_label} ({category or 'all'}/{region or 'all'}) → {out_path}")


async def build_daily(date: datetime) -> None:
    start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    date_str = date.strftime(_DATE_FMT["day"])

    categories_and_regions: list[tuple[str | None, str | None]] = [(None, None)]
    async with async_session_factory() as session:
        result = await session.execute(
            select(Article.category, Article.region)
            .where(Article.published_at >= start)
            .where(Article.published_at < end)
            .distinct()
        )
        for row in result.fetchall():
            cat, reg = row
            if (cat, reg) not in categories_and_regions:
                categories_and_regions.append((cat, reg))

    period_label = f"Daily Roundup — {date.strftime('%d %b %Y')}"

    for cat, reg in categories_and_regions:
        arts = await _get_articles_in_window(start, end, _TOP_LIMIT["day"], cat, reg)
        if not arts:
            continue
        article_ids = [a["id"] for a in arts]
        await _upsert_rollup("day", start, end, article_ids, cat, reg)

        for lang in ("en", "hi"):
            _render_and_save_rollup(
                lang, "day", date_str, arts, period_label, cat, reg
            )

    await _regenerate_sitemap()


async def build_weekly(start_monday: datetime) -> None:
    start = start_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    date_str = start.strftime(_DATE_FMT["week"])

    daily_rollups: list[Rollup] = []
    async with async_session_factory() as session:
        result = await session.execute(
            select(Rollup).where(
                Rollup.period == "day",
                Rollup.start_at >= start,
                Rollup.start_at < end,
            )
        )
        daily_rollups = list(result.scalars().all())

    if not daily_rollups:
        log.warning(f"No daily rollups found for week starting {start}")
        return

    categories_and_regions: list[tuple[str | None, str | None]] = [(None, None)]
    for r in daily_rollups:
        if (r.category, r.region) not in categories_and_regions:
            categories_and_regions.append((r.category, r.region))

    period_label = f"Weekly Roundup — w/c {start.strftime('%d %b %Y')}"

    for cat, reg in categories_and_regions:
        week_rollups = [r for r in daily_rollups if r.category == cat and r.region == reg]
        if not week_rollups:
            continue

        article_scores: dict[int, float] = {}
        for dr in week_rollups:
            article_ids = dr.article_ids
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Cluster.id, Cluster.current_score)
                    .join(Article, Cluster.id == Article.cluster_id)
                    .where(Article.id.in_(article_ids))
                )
                for art_id, score in result.fetchall():
                    art_id_int = int(art_id)
                    if art_id_int not in article_scores:
                        article_scores[art_id_int] = 0.0
                    article_scores[art_id_int] += float(score)

        days_present = len(week_rollups)
        adjusted = {
            aid: score / sqrt(days_present)
            for aid, score in article_scores.items()
        }
        sorted_ids = sorted(adjusted, key=lambda x: adjusted[x], reverse=True)

        top_ids: list[int] = []
        async with async_session_factory() as session:
            for aid in sorted_ids[: _TOP_LIMIT["week"]]:
                result = await session.execute(
                    select(Article).where(Article.id == aid)
                )
                art = result.scalar_one_or_none()
                if art:
                    top_ids.append(aid)

        await _upsert_rollup("week", start, end, top_ids, cat, reg)

        arts = []
        for aid in top_ids:
            async with async_session_factory() as session:
                result = await session.execute(select(Article).where(Article.id == aid))
                art = result.scalar_one_or_none()
                if art:
                    arts.append(
                        {
                            "id": art.id,
                            "slug": art.slug,
                            "title_en": art.title_en,
                            "title_hi": art.title_hi,
                            "summary_en": art.summary_en,
                            "summary_hi": art.summary_hi,
                            "hero_image_url": art.hero_image_url,
                            "hero_image_credit": art.hero_image_credit,
                            "category": art.category,
                            "region": art.region,
                            "published_at": art.published_at,
                            "score": adjusted.get(art.id, 0.0),
                        }
                    )

        for lang in ("en", "hi"):
            _render_and_save_rollup(
                lang, "week", date_str, arts, period_label, cat, reg
            )


async def build_monthly(year: int, month: int) -> None:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    date_str = f"{year}-{month:02d}"

    daily_rollups: list[Rollup] = []
    async with async_session_factory() as session:
        result = await session.execute(
            select(Rollup).where(
                Rollup.period == "day",
                Rollup.start_at >= start,
                Rollup.start_at < end,
            )
        )
        daily_rollups = list(result.scalars().all())

    categories_and_regions: list[tuple[str | None, str | None]] = [(None, None)]
    for r in daily_rollups:
        if (r.category, r.region) not in categories_and_regions:
            categories_and_regions.append((r.category, r.region))

    month_name = start.strftime("%B %Y")
    period_label = f"Monthly Roundup — {month_name}"

    for cat, reg in categories_and_regions:
        month_rollups = [r for r in daily_rollups if r.category == cat and r.region == reg]
        if not month_rollups:
            continue

        article_scores: dict[int, float] = {}
        for dr in month_rollups:
            article_ids = dr.article_ids
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Cluster.id, Cluster.current_score)
                    .join(Article, Cluster.id == Article.cluster_id)
                    .where(Article.id.in_(article_ids))
                )
                for art_id, score in result.fetchall():
                    art_id_int = int(art_id)
                    if art_id_int not in article_scores:
                        article_scores[art_id_int] = 0.0
                    article_scores[art_id_int] += float(score)

        days_present = len(month_rollups)
        adjusted = {
            aid: score / sqrt(days_present)
            for aid, score in article_scores.items()
        }
        sorted_ids = sorted(adjusted, key=lambda x: adjusted[x], reverse=True)

        top_ids: list[int] = []
        for aid in sorted_ids[: _TOP_LIMIT["month"]]:
            async with async_session_factory() as session:
                result = await session.execute(select(Article).where(Article.id == aid))
                art = result.scalar_one_or_none()
                if art:
                    top_ids.append(aid)

        await _upsert_rollup("month", start, end, top_ids, cat, reg)

        arts = []
        for aid in top_ids:
            async with async_session_factory() as session:
                result = await session.execute(select(Article).where(Article.id == aid))
                art = result.scalar_one_or_none()
                if art:
                    arts.append(
                        {
                            "id": art.id,
                            "slug": art.slug,
                            "title_en": art.title_en,
                            "title_hi": art.title_hi,
                            "summary_en": art.summary_en,
                            "summary_hi": art.summary_hi,
                            "hero_image_url": art.hero_image_url,
                            "hero_image_credit": art.hero_image_credit,
                            "category": art.category,
                            "region": art.region,
                            "published_at": art.published_at,
                            "score": adjusted.get(art.id, 0.0),
                        }
                    )

        for lang in ("en", "hi"):
            _render_and_save_rollup(
                lang, "month", date_str, arts, period_label, cat, reg
            )


async def build_yearly(year: int) -> None:
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    date_str = str(year)

    daily_rollups: list[Rollup] = []
    async with async_session_factory() as session:
        result = await session.execute(
            select(Rollup).where(
                Rollup.period == "day",
                Rollup.start_at >= start,
                Rollup.start_at < end,
            )
        )
        daily_rollups = list(result.scalars().all())

    categories_and_regions: list[tuple[str | None, str | None]] = [(None, None)]
    for r in daily_rollups:
        if (r.category, r.region) not in categories_and_regions:
            categories_and_regions.append((r.category, r.region))

    period_label = f"Yearly Roundup — {year}"

    for cat, reg in categories_and_regions:
        year_rollups = [r for r in daily_rollups if r.category == cat and r.region == reg]
        if not year_rollups:
            continue

        article_scores: dict[int, float] = {}
        for dr in year_rollups:
            article_ids = dr.article_ids
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Cluster.id, Cluster.current_score)
                    .join(Article, Cluster.id == Article.cluster_id)
                    .where(Article.id.in_(article_ids))
                )
                for art_id, score in result.fetchall():
                    art_id_int = int(art_id)
                    if art_id_int not in article_scores:
                        article_scores[art_id_int] = 0.0
                    article_scores[art_id_int] += float(score)

        days_present = len(year_rollups)
        adjusted = {
            aid: score / sqrt(days_present)
            for aid, score in article_scores.items()
        }
        sorted_ids = sorted(adjusted, key=lambda x: adjusted[x], reverse=True)

        top_ids: list[int] = []
        for aid in sorted_ids[: _TOP_LIMIT["year"]]:
            async with async_session_factory() as session:
                result = await session.execute(select(Article).where(Article.id == aid))
                art = result.scalar_one_or_none()
                if art:
                    top_ids.append(aid)

        await _upsert_rollup("year", start, end, top_ids, cat, reg)

        arts = []
        for aid in top_ids:
            async with async_session_factory() as session:
                result = await session.execute(select(Article).where(Article.id == aid))
                art = result.scalar_one_or_none()
                if art:
                    arts.append(
                        {
                            "id": art.id,
                            "slug": art.slug,
                            "title_en": art.title_en,
                            "title_hi": art.title_hi,
                            "summary_en": art.summary_en,
                            "summary_hi": art.summary_hi,
                            "hero_image_url": art.hero_image_url,
                            "hero_image_credit": art.hero_image_credit,
                            "category": art.category,
                            "region": art.region,
                            "published_at": art.published_at,
                            "score": adjusted.get(art.id, 0.0),
                        }
                    )

        for lang in ("en", "hi"):
            _render_and_save_rollup(
                lang, "year", date_str, arts, period_label, cat, reg
            )


async def _regenerate_sitemap() -> None:
    base_url = settings.SITE_BASE_URL or "https://example.com"
    static_dir = Path(settings.STATIC_DIR)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Article).where(Article.verifier_passed).order_by(Article.published_at.desc()).limit(1000)
        )
        articles = list(result.scalars().all())

    urls = []
    for art in articles:
        urls.append(f"{base_url}/en/article/{art.slug}")
        if art.title_hi:
            urls.append(f"{base_url}/hi/article/{art.slug}")

    sitemap_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url in urls:
        sitemap_lines.append(f"  <url><loc>{url}</loc></url>")
    sitemap_lines.append("</urlset>")

    out_path = static_dir / "sitemap.xml"
    with open(out_path, "w") as f:
        f.write("\n".join(sitemap_lines))
    log.info(f"Regenerated sitemap with {len(urls)} URLs")


async def backfill_rollups(days: int) -> None:
    from datetime import timedelta
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(1, days + 1):
        day = today - timedelta(days=i)
        log.info(f"Backfilling daily rollup for {day.strftime('%Y-%m-%d')}")
        await build_daily(day)
