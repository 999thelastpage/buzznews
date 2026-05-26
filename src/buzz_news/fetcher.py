import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from buzz_news.db import async_session_factory
from buzz_news.models import Source, RawItem
from buzz_news.sources import fetch_source
from buzz_news.normalizer import normalize

log = logging.getLogger("buzz_news.fetcher")

MAX_CONCURRENCY = 10

# Exponential cooldown for failing sources. `enabled` is operator-only —
# failures never flip it. Instead we skip a source for `_cooldown_seconds`
# after its last failed attempt. Any successful fetch resets fail_count.
COOLDOWN_GRACE_FAILURES = 3       # below this, retry every cycle
COOLDOWN_BASE_MINUTES = 30        # first cooldown after the grace period
COOLDOWN_MAX_MINUTES = 24 * 60    # cap


def _cooldown_seconds(fail_count: int) -> int:
    if fail_count < COOLDOWN_GRACE_FAILURES:
        return 0
    over = fail_count - COOLDOWN_GRACE_FAILURES
    minutes = min(COOLDOWN_BASE_MINUTES * (2 ** over), COOLDOWN_MAX_MINUTES)
    return minutes * 60


def _in_cooldown(source: Source, now: datetime) -> bool:
    cooldown = _cooldown_seconds(source.fail_count)
    if cooldown == 0 or source.last_fetched_at is None:
        return False
    return (now - source.last_fetched_at).total_seconds() < cooldown


async def run_once() -> int:
    log.info("Starting fetch cycle")

    async with async_session_factory() as session:
        result = await session.execute(
            select(Source).where(Source.enabled)
        )
        all_enabled = list(result.scalars().all())

    now = datetime.now(timezone.utc)
    sources = [s for s in all_enabled if not _in_cooldown(s, now)]
    skipped = [s for s in all_enabled if _in_cooldown(s, now)]
    if skipped:
        log.info(
            "Skipping %d sources in cooldown: %s",
            len(skipped),
            ", ".join(f"{s.slug}(fail={s.fail_count})" for s in skipped),
        )

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def fetch_one(source: Source) -> tuple[Source, int, list[str]]:
        async with sem:
            async with httpx.AsyncClient() as http:
                try:
                    candidates = await fetch_source(source, http)
                except Exception as e:
                    fail_count = source.fail_count + 1
                    next_try_min = _cooldown_seconds(fail_count) // 60
                    log.warning(
                        "Failed to fetch source %s (%s) [fail=%d, next try in ~%dm]",
                        source.slug, type(e).__name__, fail_count, next_try_min,
                    )
                    async with async_session_factory() as sess:
                        await sess.execute(
                            update(Source)
                            .where(Source.id == source.id)
                            .values(
                                fail_count=fail_count,
                                last_fetched_at=datetime.now(timezone.utc),
                            )
                        )
                        await sess.commit()
                    return source, 0, [str(e)]

                if not candidates:
                    async with async_session_factory() as sess:
                        await sess.execute(
                            update(Source)
                            .where(Source.id == source.id)
                            .values(
                                fail_count=0,
                                last_fetched_at=datetime.now(timezone.utc),
                            )
                        )
                        await sess.commit()
                    return source, 0, []

                normalized = []
                for c in candidates:
                    norm = await normalize(source, c, http)
                    normalized.append((c, norm))

                items_to_insert = []
                for candidate, norm in normalized:
                    items_to_insert.append({
                        "source_id": source.id,
                        "external_id": candidate.external_id,
                        "url": candidate.url,
                        "title": candidate.title,
                        "snippet": candidate.snippet,
                        "body": norm["body"],
                        "language": norm["language"],
                        "published_at": candidate.published_at or datetime.now(timezone.utc),
                        "fetched_at": datetime.now(timezone.utc),
                    })

                async with async_session_factory() as sess:
                    for item in items_to_insert:
                        try:
                            await sess.execute(
                                pg_insert(RawItem).values(**item).on_conflict_do_nothing(
                                    constraint="raw_items_source_id_external_id_key"
                                )
                            )
                        except Exception as ex:
                            log.debug(f"Skipping duplicate/error for {item.get('url', 'unknown')[:80]}: {ex}")
                    await sess.commit()
                    log.info(f"Inserted {len(items_to_insert)} items for source {source.slug}")

                async with async_session_factory() as sess:
                    await sess.execute(
                        update(Source)
                        .where(Source.id == source.id)
                        .values(
                            fail_count=0,
                            last_fetched_at=datetime.now(timezone.utc),
                        )
                    )
                    await sess.commit()

                return source, len(items_to_insert), []

    tasks = [fetch_one(s) for s in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total_items = 0
    failed_sources = []
    for r in results:
        if isinstance(r, Exception):
            log.error(f"Task exception: {r}")
            continue
        src, count, errors = r
        total_items += count
        if errors:
            failed_sources.append(src.slug)

    log.info(f"Fetch cycle complete: {total_items} new items from {len(sources)} sources")
    if failed_sources:
        log.warning(f"Failed sources: {failed_sources}")
    return total_items
