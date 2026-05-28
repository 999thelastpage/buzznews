from datetime import datetime, timezone

import numpy as np
from sqlalchemy.dialects import postgresql

from buzz_news.clusterer import (
    CENTROID_FREEZE_AFTER,
    COSINE_DISTANCE_THRESHOLD,
    MAX_CLUSTER_SIZE,
    _cosine_distance,
    _normalize,
    _candidate_cluster_stmt,
)


def test_threshold_phase8_value():
    # Audit showed [0.20, 0.25) was the modal attach bucket and largely
    # off-event ride-alongs. Locked at 0.18 unless re-audited.
    assert COSINE_DISTANCE_THRESHOLD == 0.18


def test_centroid_freeze_after_value():
    # Locked at 3 — the first three items define the event.
    assert CENTROID_FREEZE_AFTER == 3


def test_max_cluster_size_value():
    assert MAX_CLUSTER_SIZE == 25


def test_cosine_distance_same_vectors():
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert abs(_cosine_distance(v, v)) < 1e-5


def test_cosine_distance_orthogonal_vectors():
    v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    v2 = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    dist = _cosine_distance(v1, v2)
    assert abs(dist - 1.0) < 1e-5


def test_cosine_distance_opposite_vectors():
    v1 = np.array([1.0, 0.0], dtype=np.float32)
    v2 = np.array([-1.0, 0.0], dtype=np.float32)
    dist = _cosine_distance(v1, v2)
    assert abs(dist - 2.0) < 1e-5


def test_normalize_preserves_direction():
    v = np.array([3.0, 4.0], dtype=np.float32)
    normalized = _normalize(v)
    assert abs(np.linalg.norm(normalized) - 1.0) < 1e-5
    assert normalized[0] == 0.6
    assert normalized[1] == 0.8


def test_normalize_zero_vector():
    v = np.array([0.0, 0.0], dtype=np.float32)
    normalized = _normalize(v)
    assert np.all(np.isnan(normalized)) or np.all(normalized == 0.0)


def test_candidate_cluster_stmt_allows_article_backed_published_clusters():
    sql = str(
        _candidate_cluster_stmt(datetime(2026, 5, 28, tzinfo=timezone.utc)).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LEFT OUTER JOIN articles" in sql
    assert "clusters.is_published = false OR articles.id IS NOT NULL" in sql


def test_candidate_cluster_stmt_keeps_recent_window():
    sql = str(
        _candidate_cluster_stmt(datetime(2026, 5, 28, tzinfo=timezone.utc)).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "clusters.last_seen_at >= '2026-05-26 00:00:00+00:00'" in sql
