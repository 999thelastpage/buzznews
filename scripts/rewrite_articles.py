"""Re-write all published articles via the LLM and re-render their HTML.
Used after fixing the summary_en truncation bug — existing rows had only
500 chars of body, so we regenerate full bodies and overwrite both the
DB row and the static HTML file."""
import asyncio
import logging
from pathlib import Path

from sqlalchemy import select, update

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.models import Article, ArticleSource, RawItem
from buzz_news.publisher import _render_article, _render_hindi_article_or_fallback
from buzz_news.writer import write_article

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("rewrite")
settings = get_settings()


async def main() -> int:
    written = 0
    static_dir = Path(settings.STATIC_DIR)
    async with async_session_factory() as session:
        articles = (await session.execute(select(Article))).scalars().all()

    log.info(f"Rewriting {len(articles)} articles")

    for art in articles:
        try:
            draft = await write_article(art.cluster_id)
        except Exception as e:
            log.warning(f"write_article failed for cluster {art.cluster_id}: {e}")
            continue
        if not draft or not draft.body_en:
            log.warning(f"empty draft for cluster {art.cluster_id}")
            continue

        async with async_session_factory() as session:
            await session.execute(
                update(Article).where(Article.id == art.id).values(
                    title_en=draft.title_en,
                    title_hi=draft.title_hi,
                    summary_en=draft.body_en,
                    summary_hi=draft.body_hi,
                )
            )
            await session.commit()

        async with async_session_factory() as session:
            srcs = (await session.execute(
                select(
                    ArticleSource.raw_item_id,
                    ArticleSource.source_name,
                    ArticleSource.url,
                    RawItem.title,
                    RawItem.published_at,
                )
                .join(RawItem, ArticleSource.raw_item_id == RawItem.id)
                .where(ArticleSource.article_id == art.id)
                .order_by(ArticleSource.rank)
            )).all()
        seen_urls = set()
        seen_titles = set()
        seen_names = set()
        article_sources = []
        for s in srcs:
            url = s.url
            title = s.title.strip().lower() if s.title else ""
            name = s.source_name.strip() if s.source_name else ""
            if url in seen_urls or (title and title in seen_titles) or (name and name in seen_names):
                continue
            seen_urls.add(url)
            if title:
                seen_titles.add(title)
            if name:
                seen_names.add(name)
            article_sources.append({
                "raw_item_id": s.raw_item_id,
                "name": s.source_name,
                "url": s.url,
                "title": s.title,
                "published_at": s.published_at,
            })
            if len(article_sources) >= 6:
                break

        html_en = _render_article(
            art.id, "en",
            draft.title_en, draft.body_en, art.category, art.region,
            art.hero_image_url, art.hero_image_credit, article_sources,
            False, None, [], [], art.published_at,
            slug=art.slug,
        )
        out_en = static_dir / "en" / "article" / f"{art.slug}.html"
        out_en.parent.mkdir(parents=True, exist_ok=True)
        out_en.write_text(html_en)
        written += 1

        html_hi = _render_hindi_article_or_fallback(
            art.id,
            draft.title_en,
            draft.body_en,
            draft.title_hi,
            draft.body_hi,
            art.category,
            art.region,
            art.hero_image_url,
            art.hero_image_credit,
            article_sources,
            False,
            None,
            [],
            [],
            art.published_at,
            slug=art.slug,
        )
        out_hi = static_dir / "hi" / "article" / f"{art.slug}.html"
        out_hi.parent.mkdir(parents=True, exist_ok=True)
        out_hi.write_text(html_hi, encoding="utf-8")
        written += 1

        log.info(f"  cluster={art.cluster_id} en_words={len(draft.body_en.split())} hi_words={len((draft.body_hi or '').split())}")

    log.info(f"Wrote {written} article files")
    return written


if __name__ == "__main__":
    asyncio.run(main())
