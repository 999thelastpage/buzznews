import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.scheduler")

_scheduler: AsyncIOScheduler | None = None


def _wrap(name: str, coro_func):
    async def _job():
        try:
            await coro_func()
        except Exception:
            log.exception(f"Job {name} failed")
    return _job


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    fetch_min = max(1, settings.FETCH_INTERVAL_MIN)
    score_min = max(1, settings.SCORE_INTERVAL_MIN)
    publish_min = max(1, settings.PUBLISH_INTERVAL_MIN)

    scheduler.add_job(
        _wrap("fetch", _run_fetch),
        trigger=IntervalTrigger(minutes=fetch_min),
        id="fetch",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("embed", _run_embed),
        trigger=IntervalTrigger(minutes=score_min),
        id="embed",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("cluster", _run_cluster),
        trigger=IntervalTrigger(minutes=score_min),
        id="cluster",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("score", _run_score),
        trigger=IntervalTrigger(minutes=score_min),
        id="score",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("write", _run_write),
        trigger=IntervalTrigger(minutes=publish_min),
        id="write",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("publish", _run_publish),
        trigger=IntervalTrigger(minutes=publish_min),
        id="publish",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("daily_rollup", _run_daily_rollup),
        trigger=CronTrigger(hour=0, minute=5, timezone="Asia/Kolkata"),
        id="daily_rollup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("weekly_rollup", _run_weekly_rollup),
        trigger=CronTrigger(hour=0, minute=15, day_of_week="mon", timezone="Asia/Kolkata"),
        id="weekly_rollup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("monthly_rollup", _run_monthly_rollup),
        trigger=CronTrigger(hour=0, minute=30, day=1, timezone="Asia/Kolkata"),
        id="monthly_rollup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("retention", _run_retention),
        trigger=CronTrigger(hour=3, minute=0, timezone="Asia/Kolkata"),
        id="retention",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    return scheduler


async def _run_fetch():
    from buzz_news.fetcher import run_once
    count = await run_once()
    log.info(f"[scheduler] fetch complete: {count} new items")


async def _run_embed():
    from buzz_news.clusterer import embed_unclustered_items
    count = await embed_unclustered_items()
    log.info(f"[scheduler] embedded {count} items")


async def _run_cluster():
    from buzz_news.clusterer import run_once, sanity_sweep
    count = await run_once()
    log.info(f"[scheduler] clustered {count} items")
    merged = await sanity_sweep()
    log.info(f"[scheduler] sanity sweep merged {merged} clusters")


async def _run_score():
    from buzz_news.scorer import score_all_recent
    from buzz_news.buzz import detect_and_fire
    scored = await score_all_recent()
    log.info(f"[scheduler] scored {scored} clusters")
    fired = await detect_and_fire()
    log.info(f"[scheduler] buzz fired {fired} events")


async def _run_write():
    from buzz_news.writer import write_article
    from buzz_news.db import async_session_factory
    from buzz_news.models import Cluster
    from sqlalchemy import select

    async with async_session_factory() as session:
        result = await session.execute(
            select(Cluster)
            .where(Cluster.is_published == False)  # noqa: E712
            .where(Cluster.current_score > 0)
            .order_by(Cluster.current_score.desc())
            .limit(settings.TOP_N_PER_CYCLE)
        )
        clusters = list(result.scalars().all())

    written = 0
    for cluster in clusters:
        draft = await write_article(cluster.id)
        if draft and draft.body_en:
            written += 1
    log.info(f"[scheduler] wrote {written} article drafts")


async def _run_publish():
    from buzz_news.publisher import publish_top_n
    n = await publish_top_n(settings.TOP_N_PER_CYCLE)
    log.info(f"[scheduler] published {n} articles")


async def _run_daily_rollup():
    from buzz_news.rollups import build_daily
    today = datetime.now(timezone.utc).date()
    await build_daily(today)
    log.info(f"[scheduler] daily rollup built for {today}")


async def _run_weekly_rollup():
    from buzz_news.rollups import build_weekly
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    await build_weekly(monday)
    log.info(f"[scheduler] weekly rollup built for week of {monday}")


async def _run_monthly_rollup():
    from buzz_news.rollups import build_monthly
    now = datetime.now(timezone.utc)
    await build_monthly(now.year, now.month)
    log.info(f"[scheduler] monthly rollup built for {now.year}-{now.month:02d}")


async def _run_retention():
    from buzz_news.cli import cmd_retention_cleanup
    class FakeArgs:
        pass
    await cmd_retention_cleanup(FakeArgs())
    log.info("[scheduler] retention cleanup complete")


def start() -> None:
    global _scheduler
    _scheduler = build_scheduler()
    _scheduler.start()
    log.info("Scheduler started")


def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Scheduler stopped")
