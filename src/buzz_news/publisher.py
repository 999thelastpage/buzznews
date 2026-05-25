import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, select, update

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.models import Article, ArticleSource, Cluster, RawItem, Source
from buzz_news.writer import write_article
from buzz_news.verifier import verify_en, verify_hi
from buzz_news.imager import pick_image

settings = get_settings()
log = logging.getLogger("buzz_news.publisher")


async def _build_source_corpus(cluster_id: int) -> str:
    parts = []
    async with async_session_factory() as session:
        result = await session.execute(
            select(RawItem.title, RawItem.body)
            .where(RawItem.cluster_id == cluster_id)
            .limit(20)
        )
        for row in result.fetchall():
            if row.title:
                parts.append(row.title)
            if row.body:
                parts.append(row.body[:500])
    return " ".join(parts)


def _slugify(title: str, cluster_id: int) -> str:
    from slugify import slugify
    base = slugify(title, lowercase=True, max_length=80)
    return f"{base}-{cluster_id}"


def _interleave_categories(articles: list[dict]) -> list[dict]:
    """Reorder a score-ranked list so adjacent tiles have different categories.
    Greedy: keep position 0 (the lead), then for each next slot prefer the
    highest-scored remaining article whose category differs from the previous.
    Fall back to highest-scored if every remaining article shares the previous
    tile's category."""
    if len(articles) <= 2:
        return list(articles)
    out = [articles[0]]
    remaining = list(articles[1:])
    while remaining:
        prev_cat = out[-1].get("category")
        pick_idx = next(
            (i for i, a in enumerate(remaining) if a.get("category") != prev_cat),
            0,
        )
        out.append(remaining.pop(pick_idx))
    return out


def _compute_tile_sizes(articles: list[dict]) -> list[dict]:
    """Rank-based tile sizing per Design.md §2.1:
    top 1 → 2x2 (the lead), next up to 5 → 2x1, rest → 1x1.
    Assumes articles are already sorted by score desc."""
    result = []
    for rank, art in enumerate(articles):
        if rank == 0:
            art["tile_size"] = "2x2"
        elif rank <= 5:
            art["tile_size"] = "2x1"
        else:
            art["tile_size"] = "1x1"
        art["is_hot"] = art.get("is_hot", False)
        result.append(art)
    return result


def _render(template_name: str, **ctx) -> str:
    from jinja2 import Environment, FileSystemLoader
    template_dir = Path(__file__).parent / "web" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    env.globals["utc_now"] = lambda: datetime.now(timezone.utc)
    try:
        tpl = env.get_template(template_name)
    except Exception:
        return f"Template {template_name} not found"
    return tpl.render(**ctx)


def _render_home(articles: list[dict], lang: str, cluster_count: int, published_count: int, date_str: str, archive_str: str) -> str:
    articles = _compute_tile_sizes(articles)
    articles = _interleave_categories(articles)
    labels = _get_labels(lang)
    return _render(
        "home.html",
        lang=lang,
        title=labels.get("site_name", "BuzzNews"),
        articles=articles,
        labels=labels,
        date_str=date_str,
        archive_str=archive_str,
        cluster_count=cluster_count,
        published_count=published_count,
    )


_DEVANAGARI = str.maketrans("0123456789", "०१२३४५६७८९")


def _format_date(dt: datetime, lang: str) -> str:
    en_str = dt.strftime("%d %b %Y")
    if lang == "hi":
        return en_str.translate(_DEVANAGARI)
    return en_str


def _render_article(
    article_id: int,
    lang: str,
    title: str,
    body: str,
    category: str,
    region: str,
    image_url: str | None,
    image_credit: str | None,
    article_sources: list[dict],
    is_hot: bool,
    next_sentence: str | None,
    timeline_events: list[dict],
    related_articles: list[dict],
    published_at: datetime,
    slug: str = "",
) -> str:
    return _render(
        "article.html",
        lang=lang,
        title=title,
        labels=_get_labels(lang),
        date_str=_format_date(published_at, lang),
        article={
            "id": article_id,
            "slug": slug,
            "title_en": title,
            "category": category,
            "region": region,
            "hero_image_url": image_url,
            "hero_image_credit": image_credit,
            "source_count": len(article_sources),
        },
        is_hot=is_hot,
        body_paragraphs=[p.strip() for p in body.split("\n\n") if p.strip()],
        article_sources=article_sources,
        next_sentence=next_sentence,
        timeline_events=timeline_events,
        related_articles=related_articles,
    )


def _get_labels(lang: str) -> dict:
    from buzz_news.web.i18n import get_labels
    return get_labels(lang)


async def render_home_pages(limit: int = 22) -> int:
    """Render <STATIC_DIR>/{en,hi}/index.html with the top N published articles.
    Returns the number of language files written."""
    async with async_session_factory() as session:
        # Cluster.category is more current than Article.category (the latter
        # is frozen at publish time; clusters get re-categorized later).
        # Skip articles whose title screams "LLM extraction failure".
        garbage_phrases = ("Unavailable", "Access Restrictions", "Inaccessible")
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
            )
            .join(Cluster, Article.cluster_id == Cluster.id)
            .where(*[~Article.title_en.contains(p) for p in garbage_phrases])
            .order_by(Cluster.current_score.desc())
            .limit(limit)
        )
        rows = result.fetchall()

        if not rows:
            log.warning("render_home_pages: no verified articles to render")
            return 0

        article_ids = [r.id for r in rows]
        src_result = await session.execute(
            select(ArticleSource.article_id, ArticleSource.source_name)
            .where(ArticleSource.article_id.in_(article_ids))
            .order_by(ArticleSource.article_id, ArticleSource.rank)
        )
        names_by_article: dict[int, list[str]] = {}
        for art_id, name in src_result.fetchall():
            names_by_article.setdefault(art_id, []).append(name)

    def _to_dict(row, lang: str) -> dict | None:
        if lang == "hi" and not row.title_hi:
            return None
        names = names_by_article.get(row.id, [])
        return {
            "id": row.id,
            "slug": row.slug,
            "title_en": row.title_en,
            "title_hi": row.title_hi,
            "category": row.category or "general",
            "region": row.region,
            "hero_image_url": row.hero_image_url,
            "published_at": row.published_at,
            "score": float(row.current_score or 0),
            "source_count": row.source_count or len(names) or 1,
            "source_names": names,
        }

    async with async_session_factory() as session:
        cluster_count_row = await session.execute(
            select(func.count(Cluster.id))
        )
        cluster_count = cluster_count_row.scalar() or 0
        published_count_row = await session.execute(
            select(func.count(Article.id))
        )
        published_count = published_count_row.scalar() or 0

    now = datetime.now(timezone.utc)
    static_dir = Path(settings.STATIC_DIR)
    written = 0

    for lang in ("en", "hi"):
        articles = [a for a in (_to_dict(r, lang) for r in rows) if a is not None]
        if not articles:
            continue
        # The archive tile links to the most recently produced daily rollup.
        # Today's rollup doesn't exist until the cron fires at midnight IST,
        # so linking to today's date 404s for most of the day.
        archive_dir = static_dir / lang / "archive" / "day"
        archive_files = sorted(archive_dir.glob("*.html"), reverse=True) if archive_dir.exists() else []
        archive_str = archive_files[0].stem if archive_files else ""
        html = _render_home(
            articles=articles,
            lang=lang,
            cluster_count=cluster_count,
            published_count=published_count,
            date_str=_format_date(now, lang),
            archive_str=archive_str,
        )
        out_path = static_dir / lang / "index.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(html)
        log.info(f"Rendered home page: {out_path} ({len(articles)} tiles)")
        written += 1
    return written


async def publish_top_n(n: int = 10) -> int:
    published = 0

    async with async_session_factory() as session:
        result = await session.execute(
            select(Cluster)
            .where(Cluster.is_published == False)  # noqa: E712
            .where(Cluster.current_score > 0)
            .order_by(Cluster.current_score.desc())
            .limit(n)
        )
        clusters = list(result.scalars().all())

    for cluster in clusters:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Article).where(Article.cluster_id == cluster.id)
            )
            existing = result.scalar_one_or_none()

            if existing and existing.updated_at:
                age = datetime.now(timezone.utc) - existing.updated_at
                if age.total_seconds() < 7200:
                    continue

        draft = await write_article(cluster.id)
        if not draft or not draft.body_en:
            log.warning(f"No draft for cluster {cluster.id}, skipping")
            continue

        source_corpus = await _build_source_corpus(cluster.id)

        en_passed, en_unverified = verify_en(draft.body_en, source_corpus)
        hi_passed = True
        hi_unverified: list[str] = []
        if draft.body_hi:
            hi_passed, hi_unverified = verify_hi(draft.body_hi, draft.body_en, source_corpus)

        verifier_notes = {"en_unverified": en_unverified, "hi_unverified": hi_unverified}
        verifier_passed = en_passed and hi_passed

        hero_url = None
        hero_credit = None
        if verifier_passed:
            hero_url, hero_credit = await pick_image(cluster.id, draft.title_en, draft.body_en)

        # Reuse existing slug on republish so URLs stay stable across LLM
        # title rewordings; only compute a fresh slug for brand-new articles.
        slug = existing.slug if existing else _slugify(draft.title_en, cluster.id)

        async with async_session_factory() as session:
            cat_result = await session.execute(
                select(Cluster.category, Cluster.region)
                .where(Cluster.id == cluster.id)
            )
            cat_row = cat_result.fetchone()
            category = cat_row[0] if cat_row else "general"
            region = cat_row[1] if cat_row else "GLOBAL"

        async with async_session_factory() as session:
            rows_result = await session.execute(
                select(RawItem.id, RawItem.url, RawItem.title, Source.name, RawItem.published_at)
                .select_from(RawItem)
                .join(Source, RawItem.source_id == Source.id)
                .where(RawItem.cluster_id == cluster.id)
                .limit(6)
            )
            rows = rows_result.fetchall()
            article_sources = [
                {"raw_item_id": r[0], "url": r[1], "title": r[2], "name": r[3], "published_at": r[4]}
                for r in rows
            ]

        article_record = {
            "cluster_id": cluster.id,
            "slug": slug,
            "title_en": draft.title_en,
            "title_hi": draft.title_hi,
            "summary_en": draft.body_en[:500],
            "summary_hi": draft.body_hi[:500] if draft.body_hi else None,
            "hero_image_url": hero_url,
            "hero_image_credit": hero_credit,
            "category": category,
            "region": region,
            "published_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "verifier_passed": verifier_passed,
            "verifier_notes": verifier_notes,
        }

        async with async_session_factory() as session:
            if existing:
                # `existing` was loaded in a prior session and is detached;
                # setattr on a detached object never persists. Use UPDATE.
                await session.execute(
                    update(Article)
                    .where(Article.id == existing.id)
                    .values(**article_record)
                )
                art_id = existing.id
            else:
                art = Article(**article_record)
                session.add(art)
                await session.flush()
                art_id = art.id

                for rank, src in enumerate(article_sources):
                    session.add(ArticleSource(
                        article_id=art.id,
                        raw_item_id=src["raw_item_id"],
                        source_name=src["name"],
                        url=src["url"],
                        rank=rank,
                    ))

            await session.execute(
                update(Cluster)
                .where(Cluster.id == cluster.id)
                .values(is_published=True)
            )
            await session.commit()

        is_hot = False

        rendered_en = _render_article(
            art_id, "en",
            draft.title_en, draft.body_en, category, region,
            hero_url, hero_credit, article_sources,
            is_hot, getattr(draft, "next_sentence", None),
            [], [],
            datetime.now(timezone.utc),
            slug=slug,
        )
        static_dir = Path(settings.STATIC_DIR)
        out_path_en = static_dir / "en" / "article" / f"{slug}.html"
        out_path_en.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path_en, "w") as f:
            f.write(rendered_en)

        if draft.body_hi and hi_passed:
            rendered_hi = _render_article(
                art_id, "hi",
                draft.title_hi, draft.body_hi, category, region,
                hero_url, hero_credit, article_sources,
                is_hot, getattr(draft, "next_sentence_hi", None),
                [], [],
                datetime.now(timezone.utc),
                slug=slug,
            )
            out_path_hi = static_dir / "hi" / "article" / f"{slug}.html"
            out_path_hi.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path_hi, "w") as f:
                f.write(rendered_hi)

        await _purge_cloudflare([f"/en/article/{slug}.html", f"/hi/article/{slug}.html"])

        published += 1
        log.info(f"Published article {cluster.id}: '{draft.title_en}' (verified={verifier_passed})")

    try:
        await render_home_pages()
    except Exception as e:
        log.exception(f"render_home_pages failed: {e}")

    return published


async def _purge_cloudflare(urls: list[str]) -> None:
    if not settings.CLOUDFLARE_PURGE_ENABLED:
        return
    if not settings.CLOUDFLARE_API_TOKEN or settings.CLOUDFLARE_API_TOKEN == "TODO_PRE_LAUNCH":
        return

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://api.cloudflare.com/client/v4/zones/{settings.CLOUDFLARE_ZONE_ID}/purge_cache",
                headers={"Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}"},
                json={"files": [f"{settings.SITE_BASE_URL}{u}" for u in urls]},
            )
            if resp.status_code == 200:
                log.info(f"Cloudflare purged: {urls}")
            else:
                log.warning(f"Cloudflare purge failed: {resp.status_code}")
    except Exception as e:
        log.warning(f"Cloudflare purge error: {e}")
