import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select, update

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
        result = session.execute(
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


def _render_article(article_id: int, lang: str, title: str, body: str, category: str, region: str, image_url: str | None, image_credit: str | None, sources: list[dict]) -> str:
    from jinja2 import Template
    template_path = Path(__file__).parent / "web" / "templates" / "article.html"
    if template_path.exists():
        with open(template_path) as f:
            tpl = Template(f.read())
    else:
        tpl = Template("{{ title }}: {{ body }}")

    return tpl.render(
        article_id=article_id,
        lang=lang,
        title=title,
        body=body,
        category=category,
        region=region,
        image_url=image_url,
        image_credit=image_credit,
        sources=sources,
    )


def _render_home(articles: list[dict], lang: str) -> str:
    from jinja2 import Template
    template_path = Path(__file__).parent / "web" / "templates" / "home.html"
    if template_path.exists():
        with open(template_path) as f:
            tpl = Template(f.read())
    else:
        return "Home page"
    return tpl.render(lang=lang, articles=articles)


async def publish_top_n(n: int = 10) -> int:
    published = 0

    async with async_session_factory() as session:
        result = session.execute(
            select(Cluster)
            .where(not Cluster.is_published)
            .where(Cluster.current_score > 0)
            .order_by(Cluster.current_score.desc())
            .limit(n)
        )
        clusters = list(result.scalars().all())

    for cluster in clusters:
        async with async_session_factory() as session:
            existing = session.execute(
                select(Article).where(Article.cluster_id == cluster.id)
            ).scalar_one_or_none()

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

        slug = _slugify(draft.title_en, cluster.id)

        async with async_session_factory() as session:
            result = session.execute(
                select(Cluster.category, Cluster.region)
                .where(Cluster.id == cluster.id)
            ).fetchone()
            category = result[0] if result else "general"
            region = result[1] if result else "GLOBAL"

        async with async_session_factory() as session:
            rows = session.execute(
                select(RawItem.url, RawItem.title, Source.name)
                .select_from(RawItem)
                .join(Source, RawItem.source_id == Source.id)
                .where(RawItem.cluster_id == cluster.id)
                .limit(6)
            ).fetchall()
            article_sources = [
                {"url": r[0], "title": r[1], "name": r[2]}
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
                for key, val in article_record.items():
                    setattr(existing, key, val)
            else:
                art = Article(**article_record)
                session.add(art)
                await session.flush()

                for rank, src in enumerate(article_sources):
                    session.add(ArticleSource(
                        article_id=art.id,
                        raw_item_id=0,
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

        static_dir = Path(settings.STATIC_DIR)
        rendered_en = _render_article(
            existing.id if existing else art.id, "en",
            draft.title_en, draft.body_en, category, region,
            hero_url, hero_credit, article_sources,
        )
        out_path_en = static_dir / "en" / "article" / f"{slug}.html"
        out_path_en.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path_en, "w") as f:
            f.write(rendered_en)

        if draft.body_hi and hi_passed:
            rendered_hi = _render_article(
                existing.id if existing else art.id, "hi",
                draft.title_hi, draft.body_hi, category, region,
                hero_url, hero_credit, article_sources,
            )
            out_path_hi = static_dir / "hi" / "article" / f"{slug}.html"
            out_path_hi.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path_hi, "w") as f:
                f.write(rendered_hi)

        await _purge_cloudflare([f"/en/article/{slug}.html", f"/hi/article/{slug}.html"])

        published += 1
        log.info(f"Published article {cluster.id}: '{draft.title_en}' (verified={verifier_passed})")

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
