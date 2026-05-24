import argparse
import asyncio
import sys
import logging

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
    from scripts.seed_sources import seed_sources
    log.info("Seeding sources from catalog...")
    count = await seed_sources()
    log.info(f"Seeded {count} sources")
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
    if s.TENCENT_COS_BUCKET == "TODO_PRE_LAUNCH":
        warnings.append("TENCENT_COS_BUCKET not set (pre-launch)")
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
            .where(not Cluster.is_published)
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
        log.info(f"Wrote article for cluster {cluster.id}: EN title='{draft.title_en}'")
        written += 1

    log.info(f"Write cycle complete: {written} articles drafted")
    return 0


async def cmd_publish_once(args) -> int:
    log.info("Publish once — implement Phase 5")
    return 0


async def cmd_republish_today(args) -> int:
    log.info("Republish today — implement Phase 5")
    return 0


async def cmd_rollup(args) -> int:
    log.info("Rollup — implement Phase 7")
    return 0


async def cmd_retention_cleanup(args) -> int:
    log.info("Retention cleanup — implement Phase 9")
    return 0


async def cmd_backfill_rollups(args) -> int:
    log.info("Backfill rollups — implement Phase 7")
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
    log.info("Run worker — implement Phase 7 (APScheduler)")
    return 0


async def cmd_run_web(args) -> int:
    log.info("Run web — implement Phase 6 (FastAPI)")
    return 0


COMMANDS = {
    "migrate": cmd_migrate,
    "seed-sources": cmd_seed_sources,
    "preflight": cmd_preflight,
    "fetch-once": cmd_fetch_once,
    "embed-once": cmd_embed_once,
    "cluster-once": cmd_cluster_once,
    "score-once": cmd_score_once,
    "write-once": cmd_write_once,
    "publish-once": cmd_publish_once,
    "republish-today": cmd_republish_today,
    "rollup": cmd_rollup,
    "retention-cleanup": cmd_retention_cleanup,
    "backfill-rollups": cmd_backfill_rollups,
    "split-cluster": cmd_split_cluster,
    "run-worker": cmd_run_worker,
    "run-web": cmd_run_web,
}


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m buzz_news")
    parser.add_argument("command", choices=list(COMMANDS.keys()))
    parser.add_argument("--period", choices=["day", "week", "month", "year"])
    parser.add_argument("--date")
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
