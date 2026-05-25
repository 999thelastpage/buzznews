"""Re-render all existing published article HTML files using current templates.
Reads Article + ArticleSource rows from DB and calls _render_article directly
— no LLM calls. Used to apply template fixes to already-published articles."""
import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.models import Article, ArticleSource, RawItem
from buzz_news.publisher import _render_article

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("rerender")
settings = get_settings()


async def main() -> int:
    written = 0
    static_dir = Path(settings.STATIC_DIR)
    async with async_session_factory() as session:
        articles = (await session.execute(select(Article))).scalars().all()
        for art in articles:
            srcs_rows = (await session.execute(
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
            article_sources = [
                {
                    "raw_item_id": s.raw_item_id,
                    "name": s.source_name,
                    "url": s.url,
                    "title": s.title,
                    "published_at": s.published_at,
                }
                for s in srcs_rows
            ]

            html_en = _render_article(
                art.id, "en",
                art.title_en, art.summary_en, art.category, art.region,
                art.hero_image_url, art.hero_image_credit, article_sources,
                False, None, [], [], art.published_at,
                slug=art.slug,
            )
            out_en = static_dir / "en" / "article" / f"{art.slug}.html"
            out_en.parent.mkdir(parents=True, exist_ok=True)
            out_en.write_text(html_en)
            written += 1

            if art.title_hi and art.summary_hi:
                html_hi = _render_article(
                    art.id, "hi",
                    art.title_hi, art.summary_hi, art.category, art.region,
                    art.hero_image_url, art.hero_image_credit, article_sources,
                    False, None, [], [], art.published_at,
                    slug=art.slug,
                )
                out_hi = static_dir / "hi" / "article" / f"{art.slug}.html"
                out_hi.parent.mkdir(parents=True, exist_ok=True)
                out_hi.write_text(html_hi)
                written += 1

    log.info(f"Re-rendered {written} article files")
    return written


if __name__ == "__main__":
    asyncio.run(main())
