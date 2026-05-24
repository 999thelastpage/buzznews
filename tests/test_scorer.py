from datetime import datetime, timezone, timedelta
from buzz_news.scorer import compute_score, ScoreBreakdown


def test_compute_score_basic():
    now = datetime.now(timezone.utc)
    breakdown = compute_score(
        distinct_sources=5,
        new_sources_this_cycle=2,
        source_count=5,
        authority_sum=4.0,
        tabloid_count=0,
        category="technology",
        last_seen_at=now - timedelta(hours=1),
        now=now,
        diversity_cap=8,
        time_gravity=1.5,
    )
    assert isinstance(breakdown, ScoreBreakdown)
    assert 0.0 <= breakdown.composite <= 1.5
    assert 0.0 <= breakdown.source_diversity <= 1.0
    assert 0.0 <= breakdown.authority <= 1.0
    assert 0.0 <= breakdown.time_decay <= 1.0
    assert breakdown.anti_viral_penalty == 1.0


def test_compute_score_tabloid_penalty_high():
    now = datetime.now(timezone.utc)
    breakdown = compute_score(
        distinct_sources=10,
        new_sources_this_cycle=3,
        source_count=10,
        authority_sum=3.0,
        tabloid_count=8,
        category="politics",
        last_seen_at=now - timedelta(hours=1),
        now=now,
        diversity_cap=8,
        time_gravity=1.5,
    )
    assert breakdown.anti_viral_penalty == 0.3


def test_compute_score_tabloid_penalty_medium():
    now = datetime.now(timezone.utc)
    breakdown = compute_score(
        distinct_sources=10,
        new_sources_this_cycle=3,
        source_count=10,
        authority_sum=4.0,
        tabloid_count=5,
        category="politics",
        last_seen_at=now - timedelta(hours=1),
        now=now,
        diversity_cap=8,
        time_gravity=1.5,
    )
    assert breakdown.anti_viral_penalty == 0.7


def test_compute_score_entertainment_no_penalty():
    now = datetime.now(timezone.utc)
    breakdown = compute_score(
        distinct_sources=10,
        new_sources_this_cycle=5,
        source_count=10,
        authority_sum=4.0,
        tabloid_count=9,
        category="entertainment",
        last_seen_at=now - timedelta(hours=1),
        now=now,
        diversity_cap=8,
        time_gravity=1.5,
    )
    assert breakdown.anti_viral_penalty == 1.0


def test_compute_score_time_decay_young():
    now = datetime.now(timezone.utc)
    breakdown = compute_score(
        distinct_sources=5,
        new_sources_this_cycle=1,
        source_count=5,
        authority_sum=2.5,
        tabloid_count=0,
        category="technology",
        last_seen_at=now - timedelta(minutes=10),
        now=now,
    )
    assert breakdown.time_decay > 0.3
    assert breakdown.time_decay < 0.5


def test_compute_score_time_decay_old():
    now = datetime.now(timezone.utc)
    breakdown = compute_score(
        distinct_sources=5,
        new_sources_this_cycle=1,
        source_count=5,
        authority_sum=2.5,
        tabloid_count=0,
        category="technology",
        last_seen_at=now - timedelta(hours=24),
        now=now,
    )
    assert breakdown.time_decay < 0.1


def test_compute_score_zero_sources():
    now = datetime.now(timezone.utc)
    breakdown = compute_score(
        distinct_sources=0,
        new_sources_this_cycle=0,
        source_count=0,
        authority_sum=0.0,
        tabloid_count=0,
        category="technology",
        last_seen_at=now,
        now=now,
    )
    assert breakdown.authority == 0.0
    assert breakdown.velocity == 0.0
    assert breakdown.composite == 0.0
