import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import pow

from sqlalchemy import func, select, update

from buzz_news.db import async_session_factory
from buzz_news.models import Cluster, ClusterScore, RawItem
from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.scorer")


@dataclass
class ScoreBreakdown:
    source_diversity: float
    velocity: float
    authority: float
    time_decay: float
    anti_viral_penalty: float
    composite: float


def compute_score(
    *,
    distinct_sources: int,
    new_sources_this_cycle: int,
    source_count: int,
    authority_sum: float,
    tabloid_count: int,
    category: str,
    last_seen_at: datetime,
    now: datetime,
    diversity_cap: int | None = None,
    time_gravity: float | None = None,
) -> ScoreBreakdown:
    if diversity_cap is None:
        diversity_cap = settings.SCORE_DIVERSITY_CAP
    if time_gravity is None:
        time_gravity = settings.SCORE_TIME_GRAVITY

    diversity = min(distinct_sources, diversity_cap) / diversity_cap

    authority = (authority_sum / source_count) if source_count else 0.0

    velocity = (new_sources_this_cycle / source_count) if source_count else 0.0

    age_hours = max((now - last_seen_at).total_seconds() / 3600.0, 0.0)
    time_decay = 1.0 / pow(age_hours + 2.0, time_gravity)

    anti_viral = 1.0
    if category != "entertainment" and source_count:
        tabloid_ratio = tabloid_count / source_count
        if tabloid_ratio > 0.7:
            anti_viral = 0.3
        elif tabloid_ratio > 0.4:
            anti_viral = 0.7

    composite = diversity * authority * (1.0 + velocity) * time_decay * anti_viral

    return ScoreBreakdown(
        source_diversity=diversity,
        velocity=velocity,
        authority=authority,
        time_decay=time_decay,
        anti_viral_penalty=anti_viral,
        composite=composite,
    )


async def score_all_recent(window_hours: int = 48) -> int:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Cluster).where(Cluster.last_seen_at >= cutoff)
        )
        clusters = list(result.scalars().all())

    scored = 0
    for cluster in clusters:
        async with async_session_factory() as session:
            prev_result = await session.execute(
                select(ClusterScore)
                .where(ClusterScore.cluster_id == cluster.id)
                .order_by(ClusterScore.computed_at.desc())
                .limit(1)
            )
            prev_score = prev_result.scalar_one_or_none()

            if prev_score:
                # Velocity = new items (any source) since the last score tick.
                # Same-source follow-ups count too — that's the user-visible
                # signal for breaking-story development. Re-fetches that grow
                # an existing body bump RawItem.fetched_at via fetcher's
                # on_conflict_do_update, so they also lift velocity.
                new_count_row = await session.execute(
                    select(func.count(RawItem.id))
                    .where(RawItem.cluster_id == cluster.id)
                    .where(RawItem.fetched_at > prev_score.computed_at)
                )
                new_sources = int(new_count_row.scalar() or 0)
            else:
                # Ignition: first-ever score for this cluster — treat the
                # initial item count as fully new.
                new_sources = cluster.source_count or 0

            breakdown = compute_score(
                distinct_sources=cluster.distinct_sources or 0,
                new_sources_this_cycle=new_sources,
                source_count=cluster.source_count or 0,
                authority_sum=float(cluster.authority_sum or 0),
                tabloid_count=cluster.tabloid_count or 0,
                category=cluster.category or "general",
                last_seen_at=cluster.last_seen_at or cutoff,
                now=now,
            )

            score_row = ClusterScore(
                cluster_id=cluster.id,
                computed_at=now,
                source_diversity=breakdown.source_diversity,
                velocity=breakdown.velocity,
                authority=breakdown.authority,
                time_decay=breakdown.time_decay,
                anti_viral_penalty=breakdown.anti_viral_penalty,
                composite=breakdown.composite,
            )
            session.add(score_row)

            await session.execute(
                update(Cluster)
                .where(Cluster.id == cluster.id)
                .values(current_score=breakdown.composite)
            )
            await session.commit()
            scored += 1

    log.info(f"Scored {scored} clusters")
    return scored
