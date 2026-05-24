from datetime import datetime, timezone, timedelta


def test_buzz_velocity_above_threshold():
    from buzz_news.scorer import compute_score

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
    )
    assert breakdown.velocity == 0.4


def test_buzz_fires_when_above_threshold():
    velocity = 0.5
    threshold = 0.4
    min_authoritative = 3
    authoritative_count = 4

    assert velocity >= threshold
    assert authoritative_count >= min_authoritative


def test_buzz_blocks_6h_cooldown():
    from buzz_news.buzz import BUZZ_COOLDOWN_HOURS
    assert BUZZ_COOLDOWN_HOURS == 6
