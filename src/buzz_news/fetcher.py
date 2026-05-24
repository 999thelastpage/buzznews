import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update
from sqlalchemy.dml import Insert

from buzz_news.db import async_session_factory
from buzz_news.models import Source, RawItem
from buzz_news.sources import fetch_source
from buzz_news.normalizer import normalize

log = logging.getLogger("buzz_news.fetcher")

MAX_CONCURRENCY = 10
FAIL_DISABLE_THRESHOLD = 5


async def run_once() -> int:
    log.info("Starting fetch cycle")

    async with async_session_factory() as session:
        result = await session.execute(
            select(Source).where(Source.enabled)
        )
        sources = list(result.scalars().all())

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def fetch_one(source: Source) -> tuple[Source, int, list[str]]:
        async with sem:
            async with httpx.AsyncClient() as http:
                try:
                    candidates = await fetch_source(source, http)
                except Exception as e:
                    log.error(f"Failed to fetch source {source.slug}: {e}")
                    fail_count = source.fail_count + 1
                    new_enabled = fail_count >= FAIL_DISABLE_THRESHOLD
                    if new_enabled:
                        log.error(f"Disabling source {source.slug} after {fail_count} failures")
                    async with async_session_factory() as sess:
                        await sess.execute(
                            update(Source)
                            .where(Source.id == source.id)
                            .values(
                                fail_count=fail_count,
                                enabled=not new_enabled,
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
                            sess.execute(
                                Insert(RawItem).values(**item).on_conflict_do_nothing(
                                    constraint="raw_items_source_id_external_id_key"
                                )
                            )
                        except Exception:
                            pass
                    await sess.commit()

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
