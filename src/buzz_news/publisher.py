import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select, update

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.models import Article, ArticleSource, Cluster, RawItem, Source, ClusterScore
from buzz_news.writer import write_article
from buzz_news.verifier import verify_en, verify_hi
from buzz_news.imager import pick_image
from buzz_news.embedder import embed_text

settings = get_settings()
log = logging.getLogger("buzz_news.publisher")

IST = ZoneInfo("Asia/Kolkata")


def _ist_day_window(now_utc: datetime | None = None) -> tuple[datetime, datetime, str, str]:
    """Return (start_utc, end_utc, ist_date_str, ist_month_str) for the IST day
    containing now_utc. Used so 'today' and 'this month' archive boundaries
    match the editorial calendar (IST), not UTC."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    ist_now = now_utc.astimezone(IST)
    ist_midnight = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = ist_midnight.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(days=1)
    return start_utc, end_utc, ist_now.strftime("%Y-%m-%d"), ist_now.strftime("%Y-%m")


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
    """Rank-based tile sizing matching the mockup grid layout.
    Article 0 is the lead story (rendered separately in the template).
    Articles 1+ use a repeating 14-position cycle:
      Positions 0-2:  Row 2 — three 4-col standard cards
      Position  3:    Row 3 — 7-col large card
      Position  4:    Row 3 — 5-col large card
      Position  5:    Row 4 — 6-col bento (row-span-2) large card
      Positions 6-9:  Row 4 — four 3-col standard cards
      Position  10:   Row 5 — 4-col standard card
      Position  11:   Row 5 — 8-col large card
      Positions 12-13: Row 6 — two 6-col large cards
    """
    result = []
    for rank, art in enumerate(articles):
        if rank == 0:
            # Lead story — template handles grid placement directly
            art["col_span"] = "col-span-12 lg:col-span-8 lg:row-span-2"
            art["card_class"] = "card-huge"
        else:
            # Repeating 14-position cycle for all non-lead articles
            cycle = (rank - 1) % 14
            if cycle in (0, 1, 2):
                art["col_span"] = "col-span-12 md:col-span-6 lg:col-span-4"
                art["card_class"] = ""
            elif cycle == 3:
                art["col_span"] = "col-span-12 lg:col-span-7"
                art["card_class"] = "card-large"
            elif cycle == 4:
                art["col_span"] = "col-span-12 lg:col-span-5"
                art["card_class"] = "card-large"
            elif cycle == 5:
                art["col_span"] = "col-span-12 lg:col-span-6 lg:row-span-2"
                art["card_class"] = "card-large"
            elif cycle in (6, 7, 8, 9):
                art["col_span"] = "col-span-12 md:col-span-6 lg:col-span-3"
                art["card_class"] = ""
            elif cycle == 10:
                art["col_span"] = "col-span-12 lg:col-span-4"
                art["card_class"] = ""
            elif cycle == 11:
                art["col_span"] = "col-span-12 lg:col-span-8"
                art["card_class"] = "card-large"
            else:  # cycle in (12, 13)
                art["col_span"] = "col-span-12 lg:col-span-6"
                art["card_class"] = "card-large"

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


def _render_home(articles: list[dict], lang: str, cluster_count: int, published_count: int, date_str: str, month_str: str) -> str:
    articles = _interleave_categories(articles)
    articles = _compute_tile_sizes(articles)
    labels = _get_labels(lang)
    return _render(
        "home.html",
        lang=lang,
        title=labels.get("site_name", "BuzzNews"),
        articles=articles,
        labels=labels,
        date_str=date_str,
        month_str=month_str,
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
            "sources": article_sources,
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


def _extract_why_it_matters(summary: str | None, lang: str) -> str:
    if not summary:
        return ""
    summary = summary.strip()
    if lang == "hi":
        parts = [p.strip() for p in summary.split("।") if p.strip()]
        if parts:
            return parts[-1] + "।"
    else:
        parts = [p.strip() for p in summary.split(". ") if p.strip()]
        if parts:
            last = parts[-1]
            if last and not last.endswith((".", "?", "!")):
                last += "."
            return last
    return ""


def _get_trending_data(cluster_id: int, current_score: float, scores_by_cluster: dict[int, list[float]]) -> list[float]:
    trending = scores_by_cluster.get(cluster_id, [])
    if len(trending) < 2:
        s_val = float(current_score or 0.0)
        return [s_val, s_val]
    return [float(x) for x in trending[-10:]]


async def render_home_pages(limit: int = 15) -> int:
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
                Article.summary_en,
                Article.summary_hi,
                Cluster.category.label("category"),
                Article.region,
                Article.hero_image_url,
                Article.published_at,
                Cluster.current_score,
                Cluster.source_count,
                Article.cluster_id,
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
        cluster_ids = [r.cluster_id for r in rows]

        # Fetch sources with URLs and raw titles
        src_result = await session.execute(
            select(
                ArticleSource.article_id,
                ArticleSource.source_name,
                ArticleSource.url,
                RawItem.title
            )
            .join(RawItem, ArticleSource.raw_item_id == RawItem.id)
            .where(ArticleSource.article_id.in_(article_ids))
            .order_by(ArticleSource.article_id, ArticleSource.rank)
        )
        sources_by_article = {}
        for art_id, name, url, title in src_result.fetchall():
            srcs = sources_by_article.setdefault(art_id, [])
            if not any(s["name"] == name for s in srcs):
                srcs.append({
                    "name": name,
                    "url": url,
                    "title": title or "",
                })

        # Fetch historical composite scores for trending sparklines
        scores_result = await session.execute(
            select(ClusterScore.cluster_id, ClusterScore.composite)
            .where(ClusterScore.cluster_id.in_(cluster_ids))
            .order_by(ClusterScore.cluster_id, ClusterScore.computed_at.asc())
        )
        scores_by_cluster = {}
        for c_id, comp in scores_result.fetchall():
            scores_by_cluster.setdefault(c_id, []).append(float(comp))

    def _to_dict(row, lang: str) -> dict | None:
        if lang == "hi" and not row.title_hi:
            return None
        art_sources = sources_by_article.get(row.id, [])
        names = [s["name"] for s in art_sources]
        summary = row.summary_hi if lang == "hi" and row.summary_hi else row.summary_en
        excerpt = ""
        if summary:
            paragraphs = [p.strip() for p in summary.split("\n\n") if p.strip()]
            if paragraphs:
                excerpt = paragraphs[0]
                if len(excerpt) > 200:
                    excerpt = excerpt[:200] + "..."

        why_it_matters = _extract_why_it_matters(summary, lang)
        if not why_it_matters:
            why_it_matters = "इस घटनाक्रम पर कवरेज जारी है।" if lang == "hi" else "Coverage of this development continues."

        trending_data = _get_trending_data(row.cluster_id, row.current_score, scores_by_cluster)

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
            "sources": art_sources,
            "excerpt": excerpt,
            "why_it_matters": why_it_matters,
            "trending_data": trending_data,
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

    _, _, _, month_str = _ist_day_window(now)

    for lang in ("en", "hi"):
        articles = [a for a in (_to_dict(r, lang) for r in rows) if a is not None]
        if not articles:
            continue
        html = _render_home(
            articles=articles,
            lang=lang,
            cluster_count=cluster_count,
            published_count=published_count,
            date_str=_format_date(now, lang),
            month_str=month_str,
        )
        out_path = static_dir / lang / "index.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        log.info(f"Rendered home page: {out_path} ({len(articles)} tiles)")
        written += 1
    return written


async def render_today_pages(limit: int = 500) -> int:
    """Render <STATIC_DIR>/{en,hi}/archive/today.html with all articles
    published in the current IST day, ranked by Cluster.current_score.

    Unlike render_home_pages, this does not filter by verifier_passed —
    the archive shows the full corpus that the home page selects from.
    """
    now = datetime.now(timezone.utc)
    start_utc, end_utc, ist_date_str, month_str = _ist_day_window(now)

    async with async_session_factory() as session:
        garbage_phrases = ("Unavailable", "Access Restrictions", "Inaccessible")
        result = await session.execute(
            select(
                Article.id,
                Article.slug,
                Article.title_en,
                Article.title_hi,
                Article.summary_en,
                Article.summary_hi,
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
            .order_by(Cluster.current_score.desc())
            .limit(limit)
        )
        rows = result.fetchall()

    if not rows:
        log.warning("render_today_pages: no articles in today's IST window")
        return 0

    static_dir = Path(settings.STATIC_DIR)
    written = 0
    period_labels = {
        "en": f"Today — {now.astimezone(IST).strftime('%d %b %Y')} IST",
        "hi": f"आज — {now.astimezone(IST).strftime('%d %b %Y')} IST",
    }

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
        windows = _archive_windows("today", lang, labels, ist_date_str, month_str)

        html = _render(
            "archive.html",
            lang=lang,
            period="today",
            period_label=period_labels[lang],
            date_str=ist_date_str,
            page_key="archive/today",
            articles=articles,
            windows=windows,
            total_count=len(articles),
            labels=labels,
            og_description=f"Today's news — {len(articles)} stories",
            search_query="",
        )
        out_path = static_dir / lang / "archive" / "today.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        log.info(f"Rendered today archive: {out_path} ({len(articles)} articles)")
        written += 1

    return written


def _archive_windows(current: str, lang: str, labels: dict, today_str: str, month_str: str) -> list[dict]:
    """The 2-window archive nav: Today + This Month. Used by today + monthly
    + search archive pages so the user can pivot between them."""
    return [
        {
            "period": "today",
            "url": f"/{lang}/archive/today",
            "label": labels.get("today", "Today"),
            "meta": today_str,
        },
        {
            "period": "month",
            "url": f"/{lang}/archive/month/{month_str}",
            "label": labels.get("this_month", "This Month"),
            "meta": month_str,
        },
    ]


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
            )
            rows = rows_result.fetchall()
            seen_urls = set()
            seen_titles = set()
            seen_names = set()
            article_sources = []
            for r in rows:
                url = r[1]
                title = r[2].strip().lower() if r[2] else ""
                name = r[3].strip() if r[3] else ""
                if url in seen_urls or (title and title in seen_titles) or (name and name in seen_names):
                    continue
                seen_urls.add(url)
                if title:
                    seen_titles.add(title)
                if name:
                    seen_names.add(name)
                article_sources.append({
                    "raw_item_id": r[0],
                    "url": r[1],
                    "title": r[2],
                    "name": r[3],
                    "published_at": r[4],
                })
                if len(article_sources) >= 6:
                    break

        # Embed the article for hybrid search. Failure here must not block
        # the publish — the backfill script can fill in missing embeddings.
        embedding = None
        try:
            text_for_embed = f"{draft.title_en}\n{draft.body_en or ''}"
            embedding = embed_text(text_for_embed, "RETRIEVAL_DOCUMENT").tolist()
        except Exception as e:
            log.warning(f"embed_text failed for cluster {cluster.id}: {e}")

        article_record = {
            "cluster_id": cluster.id,
            "slug": slug,
            "title_en": draft.title_en,
            "title_hi": draft.title_hi,
            "summary_en": draft.body_en,
            "summary_hi": draft.body_hi if draft.body_hi else None,
            "hero_image_url": hero_url,
            "hero_image_credit": hero_credit,
            "category": category,
            "region": region,
            "published_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "verifier_passed": verifier_passed,
            "verifier_notes": verifier_notes,
            "embedding": embedding,
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
        with open(out_path_en, "w", encoding="utf-8") as f:
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
            with open(out_path_hi, "w", encoding="utf-8") as f:
                f.write(rendered_hi)

        await _purge_cloudflare([f"/en/article/{slug}.html", f"/hi/article/{slug}.html"])

        published += 1
        log.info(f"Published article {cluster.id}: '{draft.title_en}' (verified={verifier_passed})")

    try:
        await render_home_pages()
    except Exception as e:
        log.exception(f"render_home_pages failed: {e}")

    try:
        await render_today_pages()
    except Exception as e:
        log.exception(f"render_today_pages failed: {e}")

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
