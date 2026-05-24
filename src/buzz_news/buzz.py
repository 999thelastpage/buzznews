import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from buzz_news.db import async_session_factory
from buzz_news.models import Cluster, ClusterScore, BuzzEvent, RawItem, Source
from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.buzz")

BUZZ_COOLDOWN_HOURS = 6


async def detect_and_fire() -> int:
    now = datetime.now(timezone.utc)
    cooldown_cutoff = now - timedelta(hours=BUZZ_COOLDOWN_HOURS)

    async with async_session_factory() as session:
        recent_buzz = await session.execute(
            select(BuzzEvent.fired_at, BuzzEvent.cluster_id)
            .where(BuzzEvent.fired_at >= cooldown_cutoff)
        )
        recently_fired = set(recent_buzz.fetchall())

        result = await session.execute(
            select(Cluster).where(Cluster.is_published == False)  # noqa: E712
        )
        clusters = list(result.scalars().all())

    fired = 0
    for cluster in clusters:
        if (cluster.id,) in recently_fired:
            continue

        async with async_session_factory() as session:
            score_result = await session.execute(
                select(ClusterScore)
                .where(ClusterScore.cluster_id == cluster.id)
                .order_by(ClusterScore.computed_at.desc())
                .limit(2)
            )
            scores = list(score_result.scalars().all())

        if len(scores) < 2:
            continue

        current, previous = scores[0], scores[1]
        current_composite = float(current.composite)
        previous_composite = float(previous.composite)

        if previous_composite > 0:
            velocity = (current_composite - previous_composite) / previous_composite
        else:
            velocity = current_composite

        async with async_session_factory() as session:
            sources_result = await session.execute(
                select(Source.authority)
                .select_from(RawItem)
                .join(Source, RawItem.source_id == Source.id)
                .where(RawItem.cluster_id == cluster.id)
                .where(Source.authority >= 0.8)
                .distinct()
            )
            authoritative_count = len(list(sources_result.fetchall()))

        if (
            velocity < settings.BUZZ_VELOCITY_THRESHOLD
            or authoritative_count < settings.BUZZ_MIN_AUTHORITATIVE
        ):
            continue

        raw_items_result = await session.execute(
            select(RawItem.title, RawItem.url, Source.name)
            .select_from(RawItem)
            .join(Source, RawItem.source_id == Source.id)
            .where(RawItem.cluster_id == cluster.id)
            .limit(5)
        )
        top_items = [
            {"name": row.name, "url": row.url}
            for row in raw_items_result.fetchall()
        ]

        payload = {
            "cluster_id": cluster.id,
            "fired_at": now.isoformat(),
            "headline_guess": top_items[0]["name"] if top_items else None,
            "sources": top_items,
            "velocity": round(velocity, 4),
            "distinct_authoritative": authoritative_count,
            "composite": round(current_composite, 4),
            "category": cluster.category,
            "region": cluster.region,
        }

        event = BuzzEvent(
            cluster_id=cluster.id,
            fired_at=now,
            velocity=velocity,
            distinct_authoritative=authoritative_count,
            composite=current_composite,
            payload=payload,
            delivered=False,
        )

        async with async_session_factory() as session:
            session.add(event)
            await session.commit()

        await _deliver_webhook(payload)
        fired += 1
        log.info(f"Buzz fired for cluster {cluster.id}: velocity={velocity:.3f}, authoritative={authoritative_count}")

    log.info(f"Buzz detection complete: {fired} events fired")
    return fired


async def _deliver_webhook(payload: dict) -> bool:
    if not settings.BUZZ_WEBHOOK_URL:
        log.debug("BUZZ_WEBHOOK_URL not set, skipping delivery")
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(settings.BUZZ_WEBHOOK_URL, json=payload)
            if response.status_code < 300:
                log.info(f"Webhook delivered for cluster {payload['cluster_id']}")
                return True
            else:
                log.warning(f"Webhook delivery failed: {response.status_code}")
                return False
    except Exception as e:
        log.error(f"Webhook delivery error: {e}")
        return False
