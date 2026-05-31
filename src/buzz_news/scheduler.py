import logging
from datetime import datetime, timedelta, timezone

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
    scheduler = AsyncIOScheduler(job_defaults={
        "coalesce": True,
        "max_instances": 1,
        "misfire_grace_time": 120,
    })

    fetch_min = max(1, settings.FETCH_INTERVAL_MIN)
    embed_min = max(1, settings.EMBED_INTERVAL_MIN)
    cluster_min = max(1, settings.CLUSTER_INTERVAL_MIN)
    score_min = max(1, settings.SCORE_INTERVAL_MIN)
    publish_min = max(1, settings.PUBLISH_INTERVAL_MIN)
    sanity_min = max(15, settings.SANITY_SWEEP_INTERVAL_MIN)

    def interval(minutes: int, offset_seconds: int = 0) -> IntervalTrigger:
        return IntervalTrigger(
            minutes=minutes,
            start_date=datetime.now(timezone.utc) + timedelta(seconds=offset_seconds),
        )

    scheduler.add_job(
        _wrap("fetch", _run_fetch),
        trigger=interval(fetch_min, 0),
        id="fetch",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("embed", _run_embed),
        trigger=interval(embed_min, 120),
        id="embed",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("cluster", _run_cluster),
        trigger=interval(cluster_min, 240),
        id="cluster",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("score", _run_score),
        trigger=interval(score_min, 360),
        id="score",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("publish", _run_publish),
        trigger=interval(publish_min, 480),
        id="publish",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _wrap("sanity_sweep", _run_sanity_sweep),
        trigger=interval(sanity_min, 540),
        id="sanity_sweep",
        replace_existing=True,
    )

    scheduler.add_job(
        _wrap("monthly_archive", _run_monthly_archive),
        trigger=IntervalTrigger(hours=1, start_date=datetime.now(timezone.utc) + timedelta(seconds=660)),
        id="monthly_archive",
        replace_existing=True,
    )

    scheduler.add_job(
        _wrap("previous_month_finalize", _run_previous_month_finalize),
        trigger=CronTrigger(hour=0, minute=30, day=1, timezone="Asia/Kolkata"),
        id="previous_month_finalize",
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
    from buzz_news.clusterer import run_once
    count = await run_once()
    log.info(f"[scheduler] clustered {count} items")


async def _run_sanity_sweep():
    from buzz_news.clusterer import sanity_sweep
    merged = await sanity_sweep()
    log.info(f"[scheduler] sanity sweep merged {merged} clusters")


async def _run_score():
    from buzz_news.scorer import score_all_recent
    from buzz_news.buzz import detect_and_fire
    scored = await score_all_recent()
    log.info(f"[scheduler] scored {scored} clusters")
    fired = await detect_and_fire()
    log.info(f"[scheduler] buzz fired {fired} events")


async def _run_publish():
    from buzz_news.publisher import publish_top_n
    n = await publish_top_n(settings.TOP_N_PER_CYCLE)
    log.info(f"[scheduler] published {n} articles")


async def _run_monthly_archive():
    """Rebuild the current IST-month archive page every hour so it grows
    incrementally as articles publish through the day."""
    from buzz_news.rollups import build_monthly
    from buzz_news.publisher import IST
    now_ist = datetime.now(timezone.utc).astimezone(IST)
    await build_monthly(now_ist.year, now_ist.month)
    log.info(f"[scheduler] monthly archive rebuilt for {now_ist.year}-{now_ist.month:02d} IST")


async def _run_previous_month_finalize():
    """On the 1st of each IST month, rebuild the previous month one final time
    so any late-arriving articles get included before that page is left frozen."""
    from buzz_news.rollups import build_monthly
    from buzz_news.publisher import IST
    now_ist = datetime.now(timezone.utc).astimezone(IST)
    prev_year = now_ist.year if now_ist.month > 1 else now_ist.year - 1
    prev_month = now_ist.month - 1 if now_ist.month > 1 else 12
    await build_monthly(prev_year, prev_month)
    log.info(f"[scheduler] previous month archive finalized for {prev_year}-{prev_month:02d} IST")


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
