import argparse
import asyncio
import sys
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from buzz_news.config import get_settings

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("buzz_news.cli")


async def cmd_migrate(args) -> int:
    from buzz_news.db import engine
    from buzz_news.models import Base

    log.info("Running migrations...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Migration complete")
    return 0


async def cmd_seed_sources(args) -> int:
    import yaml
    from pathlib import Path

    from buzz_news.db import async_session_factory
    from buzz_news.models import Source

    import buzz_news as buzz_news_pkg
    catalog_path = Path(buzz_news_pkg.__file__).parent / "sources" / "catalog.yaml"
    with open(catalog_path) as f:
        data = yaml.safe_load(f)

    sources = data.get("sources", [])
    if not sources:
        log.warning("No sources found in catalog")
        return 1

    seeded = 0
    async with async_session_factory() as session:
        for src in sources:
            values = {
                "slug": src["slug"],
                "name": src["name"],
                "url": src["url"],
                "kind": src["kind"],
                "language": src["language"],
                "region": src["region"],
                "category": src["category"],
                "authority": src.get("authority", 0.5),
                "is_tabloid": src.get("is_tabloid", False),
                "enabled": src.get("enabled", True),
                "extra": src.get("extra", {}),
            }
            update_cols = {k: v for k, v in values.items() if k != "slug"}
            # Upsert keyed on slug; never sets id, so existing rows keep
            # their id (and their fetch state: etag, fail_count, ...).
            await session.execute(
                pg_insert(Source).values(**values).on_conflict_do_update(
                    constraint="sources_slug_key",
                    set_=update_cols,
                )
            )
            seeded += 1

        await session.commit()

    log.info(f"Seeded {seeded} sources from catalog")
    return 0


async def cmd_deploy_static(args) -> int:
    """Copy bundled static assets (robots.txt, privacy.html) into STATIC_DIR.
    Idempotent — safe to run on every deploy."""
    import shutil
    from pathlib import Path
    import buzz_news as pkg

    src_root = Path(pkg.__file__).parent / "web" / "static"
    dst_root = Path(settings.STATIC_DIR)
    # (source path relative to web/static/, destination path relative to STATIC_DIR)
    pairs = [
        ("robots.txt", "robots.txt"),
        ("privacy.html", "en/privacy.html"),
        ("hi/privacy.html", "hi/privacy.html"),
        ("js/ticker.js", "js/ticker.js"),
        ("js/theme.js", "js/theme.js"),
        ("js/sparkline.js", "js/sparkline.js"),
        ("js/localtime.js", "js/localtime.js"),
        ("favicon.svg", "favicon.svg"),
        ("favicon.ico", "favicon.ico"),
        ("apple-touch-icon.png", "apple-touch-icon.png"),
    ]
    copied = 0
    for src_rel, dst_rel in pairs:
        src = src_root / src_rel
        dst = dst_root / dst_rel
        if not src.exists():
            log.warning(f"deploy-static: source missing {src}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        log.info(f"deploy-static: {src} → {dst}")
        copied += 1
    log.info(f"deploy-static: {copied} files copied")
    return 0


async def cmd_preflight(args) -> int:
    from buzz_news.config import get_settings
    s = get_settings()
    errors = []
    warnings = []

    if not s.GEMINI_API_KEY:
        errors.append("GEMINI_API_KEY is required")
    if not s.DATABASE_URL or "CHANGE_ME" in s.DATABASE_URL:
        errors.append("DATABASE_URL is not configured")
    if s.SITE_HOST == "TODO_PRE_LAUNCH":
        warnings.append("SITE_HOST is not set (pre-launch)")
    if s.CLOUDFLARE_ZONE_ID == "TODO_PRE_LAUNCH":
        warnings.append("CLOUDFLARE_ZONE_ID not set (pre-launch)")
    if s.TAVILY_API_KEY == "TODO_BEFORE_PHASE_1":
        warnings.append("TAVILY_API_KEY not set (Tavily source will be skipped)")

    for w in warnings:
        log.warning(w)
    for e in errors:
        log.error(e)

    if errors:
        return 1
    log.info("Preflight OK")
    return 0


async def cmd_fetch_once(args) -> int:
    from buzz_news.fetcher import run_once
    log.info("Running fetch cycle...")
    count = await run_once()
    log.info(f"Fetch cycle complete: {count} new items")
    return 0


async def cmd_embed_once(args) -> int:
    from buzz_news.clusterer import embed_unclustered_items
    log.info("Embedding unclustered items...")
    count = await embed_unclustered_items()
    log.info(f"Embedded {count} items")
    return 0


async def cmd_cluster_once(args) -> int:
    from buzz_news.clusterer import run_once, sanity_sweep
    log.info("Running cluster cycle...")
    count = await run_once()
    log.info(f"Clustered {count} items")
    log.info("Running sanity sweep...")
    merged = await sanity_sweep()
    log.info(f"Sanity sweep merged {merged} clusters")
    return 0


async def cmd_score_once(args) -> int:
    from buzz_news.scorer import score_all_recent
    from buzz_news.buzz import detect_and_fire
    log.info("Running scoring cycle...")
    scored = await score_all_recent()
    log.info(f"Scored {scored} clusters")
    log.info("Running buzz detection...")
    fired = await detect_and_fire()
    log.info(f"Buzz detection: {fired} events fired")
    return 0


async def cmd_write_once(args) -> int:
    from buzz_news.writer import write_article
    from buzz_news.db import async_session_factory
    from buzz_news.models import Cluster
    from sqlalchemy import select

    log.info("Running write cycle...")
    async with async_session_factory() as session:
        result = await session.execute(
            select(Cluster)
            .where(Cluster.is_published == False)  # noqa: E712
            .order_by(Cluster.current_score.desc())
            .limit(settings.TOP_N_PER_CYCLE)
        )
        clusters = list(result.scalars().all())

    written = 0
    for cluster in clusters:
        draft = await write_article(cluster.id)
        if not draft:
            log.warning(f"No draft generated for cluster {cluster.id}")
            continue
        log.info(
            f"Wrote article for cluster {cluster.id}: "
            f"category={draft.category} EN title='{draft.title_en}'"
        )
        written += 1

    log.info(f"Write cycle complete: {written} articles drafted")
    return 0


async def cmd_publish_once(args) -> int:
    from buzz_news.publisher import publish_top_n
    log.info("Running publish cycle...")
    n = await publish_top_n(settings.TOP_N_PER_CYCLE)
    log.info(f"Published {n} articles")
    return 0


async def cmd_republish_today(args) -> int:
    from buzz_news.publisher import render_home_pages

    log.info("Re-rendering home pages from existing published articles...")
    written = await render_home_pages()
    log.info(f"Rendered {written} home page(s)")
    return 0


async def cmd_today_archive(args) -> int:
    from buzz_news.publisher import render_today_pages
    log.info("Building today archive...")
    n = await render_today_pages()
    log.info(f"Wrote {n} today archive file(s)")
    return 0


async def cmd_monthly_archive(args) -> int:
    from buzz_news.rollups import build_monthly
    from buzz_news.publisher import IST

    if args.month:
        parts = args.month.split("-")
        year, month = int(parts[0]), int(parts[1])
    else:
        now_ist = datetime.now(timezone.utc).astimezone(IST)
        year, month = now_ist.year, now_ist.month

    log.info(f"Building monthly archive for {year}-{month:02d} IST...")
    await build_monthly(year, month)
    return 0


async def cmd_retention_cleanup(args) -> int:
    from buzz_news.db import async_session_factory
    from buzz_news.models import RawItem, ClusterScore, BuzzEvent, SearchQueryCache

    cutoff_raw = datetime.now(timezone.utc) - timedelta(days=settings.RETENTION_RAW_ITEMS_DAYS)
    cutoff_scores = datetime.now(timezone.utc) - timedelta(days=settings.RETENTION_CLUSTER_SCORES_DAYS)
    cutoff_buzz = datetime.now(timezone.utc) - timedelta(days=settings.RETENTION_BUZZ_EVENTS_DAYS)
    cutoff_search = datetime.now(timezone.utc) - timedelta(days=settings.RETENTION_SEARCH_CACHE_DAYS)

    async with async_session_factory() as session:
        result = await session.execute(
            sa.delete(RawItem).where(RawItem.published_at < cutoff_raw)
        )
        raw_deleted = result.rowcount

        result = await session.execute(
            sa.delete(ClusterScore).where(ClusterScore.computed_at < cutoff_scores)
        )
        scores_deleted = result.rowcount

        result = await session.execute(
            sa.delete(BuzzEvent).where(BuzzEvent.fired_at < cutoff_buzz)
        )
        buzz_deleted = result.rowcount

        result = await session.execute(
            sa.delete(SearchQueryCache).where(SearchQueryCache.created_at < cutoff_search)
        )
        search_cache_deleted = result.rowcount

        await session.commit()

    images_dir = Path(settings.STATIC_DIR) / "images"
    cutoff_images = datetime.now(timezone.utc) - timedelta(days=settings.RETENTION_IMAGES_DAYS)
    images_deleted = 0
    if images_dir.exists():
        for item_dir in images_dir.iterdir():
            if item_dir.is_dir() and item_dir.stat().st_mtime < cutoff_images.timestamp():
                import shutil
                shutil.rmtree(item_dir)
                images_deleted += 1

    log.info(
        f"Retention cleanup: {raw_deleted} raw_items, {scores_deleted} cluster_scores, "
        f"{buzz_deleted} buzz_events, {search_cache_deleted} search_query_cache, "
        f"{images_deleted} image dirs deleted"
    )
    return 0


async def cmd_split_cluster(args) -> int:
    from buzz_news.clusterer import split_cluster
    if args.cluster_id is None:
        log.error("split-cluster requires a cluster_id argument")
        return 1
    item_ids = []
    if args.items:
        item_ids = [int(x.strip()) for x in args.items.split(",")]
    log.info(f"Splitting cluster {args.cluster_id}, detaching items: {item_ids}")
    count = await split_cluster(args.cluster_id, item_ids)
    log.info(f"Detached {count} items into new cluster")
    return 0


async def cmd_run_worker(args) -> int:
    from buzz_news.scheduler import start, stop
    import signal

    log.info("Starting BuzzNews worker scheduler...")
    start()

    stop_event = asyncio.Event()

    def _sigterm(signum, frame):
        log.info("Received SIGTERM, shutting down...")
        stop()
        stop_event.set()

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    log.info("Worker running. Press Ctrl+C or send SIGTERM to stop.")
    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop()
    return 0


async def cmd_run_web(args) -> int:
    import uvicorn
    log.info("Starting web server on 127.0.0.1:8000...")
    uvicorn.run(
        "buzz_news.web.app:app",
        host="127.0.0.1",
        port=8000,
        workers=1,
        log_level="info",
    )
    return 0


COMMANDS = {
    "migrate": cmd_migrate,
    "seed-sources": cmd_seed_sources,
    "preflight": cmd_preflight,
    "deploy-static": cmd_deploy_static,
    "fetch-once": cmd_fetch_once,
    "embed-once": cmd_embed_once,
    "cluster-once": cmd_cluster_once,
    "score-once": cmd_score_once,
    "write-once": cmd_write_once,
    "publish-once": cmd_publish_once,
    "republish-today": cmd_republish_today,
    "today-archive": cmd_today_archive,
    "monthly-archive": cmd_monthly_archive,
    "retention-cleanup": cmd_retention_cleanup,
    "split-cluster": cmd_split_cluster,
    "run-worker": cmd_run_worker,
    "run-web": cmd_run_web,
}


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m buzz_news")
    parser.add_argument("command", choices=list(COMMANDS.keys()))
    parser.add_argument("--month", help="YYYY-MM for monthly-archive (defaults to current IST month)")
    parser.add_argument("--items")
    parser.add_argument("cluster_id", nargs="?", type=int)
    parser.add_argument("--days", type=int, default=7)

    args = parser.parse_args()

    try:
        coro = COMMANDS[args.command](args)
        return asyncio.run(coro)
    except Exception as e:
        log.exception(f"Command {args.command} failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
