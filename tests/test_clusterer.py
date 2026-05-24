import numpy as np

from buzz_news.clusterer import _cosine_distance, _normalize


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
