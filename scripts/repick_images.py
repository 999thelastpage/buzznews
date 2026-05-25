"""Re-pick hero images for existing articles.

Run after fixing imager.py (source URL quality) and dropping the
verifier_passed gate. Two modes:

  --missing    Only articles where hero_image_url IS NULL  (default)
  --all        Every article — overwrites existing 200x133 files too
  --low-res    Articles whose on-disk hero.webp is < 800px wide

Usage:
  sudo -u ubuntu /home/ubuntu/buzznews/.venv/bin/python scripts/repick_images.py --all

Re-uses imager.pick_image() so behavior matches publish_top_n.
"""
import argparse
import asyncio
import logging
from pathlib import Path

from sqlalchemy import select, update

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.imager import pick_image
from buzz_news.models import Article

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("repick")
settings = get_settings()


def _on_disk_width(hero_url: str | None) -> int | None:
    """Return on-disk hero.webp width or None if missing/unreadable.
    `hero_url` is the web-relative URL like `/images/{cluster_id}/hero.webp`."""
    if not hero_url:
        return None
    try:
        from PIL import Image  # local import; PIL is already a runtime dep
        # Strip leading slash, join with STATIC_DIR
        path = Path(settings.STATIC_DIR) / hero_url.lstrip("/")
        if not path.exists():
            return None
        with Image.open(path) as img:
            return img.width
    except Exception:
        return None


async def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="re-pick for every article")
    mode.add_argument("--missing", action="store_true", help="only articles with hero_image_url IS NULL")
    mode.add_argument("--low-res", action="store_true", help="only articles whose on-disk hero is < 800px wide")
    parser.add_argument("--limit", type=int, default=0, help="cap on number of articles to process (0 = no cap)")
    args = parser.parse_args()

    # Default behavior when no flag given: process missing AND low-res (the
    # two states that need fixing after the image gate drop + quality fix).
    if not (args.all or args.missing or args.low_res):
        args.missing = True
        args.low_res = True

    async with async_session_factory() as session:
        result = await session.execute(
            select(Article.id, Article.cluster_id, Article.title_en, Article.summary_en, Article.hero_image_url)
            .order_by(Article.id.asc())
        )
        rows = result.all()

    candidates = []
    for r in rows:
        if args.all:
            candidates.append(r)
            continue
        is_missing = r.hero_image_url is None
        is_low_res = False
        if args.low_res and r.hero_image_url:
            w = _on_disk_width(r.hero_image_url)
            is_low_res = w is not None and w < 800
        if (args.missing and is_missing) or (args.low_res and is_low_res):
            candidates.append(r)

    if args.limit:
        candidates = candidates[: args.limit]

    log.info(f"Re-picking images for {len(candidates)} articles (of {len(rows)} total)")

    picked = 0
    failed = 0
    skipped = 0
    for i, r in enumerate(candidates, 1):
        if not r.title_en:
            skipped += 1
            continue
        try:
            hero_url, hero_credit = await pick_image(
                r.cluster_id,
                r.title_en,
                r.summary_en or "",
            )
        except Exception as e:
            log.warning(f"[{i}/{len(candidates)}] article {r.id}: pick_image raised {e}")
            failed += 1
            continue

        if not hero_url:
            log.info(f"[{i}/{len(candidates)}] article {r.id}: no image found")
            skipped += 1
            continue

        async with async_session_factory() as session:
            await session.execute(
                update(Article)
                .where(Article.id == r.id)
                .values(hero_image_url=hero_url, hero_image_credit=hero_credit)
            )
            await session.commit()
        picked += 1
        log.info(f"[{i}/{len(candidates)}] article {r.id}: {hero_url}")

    log.info(f"Done. picked={picked} failed={failed} skipped={skipped}")
    return picked


if __name__ == "__main__":
    asyncio.run(main())
