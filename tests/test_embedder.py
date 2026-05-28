from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def configure_openai_embedder(monkeypatch):
    from buzz_news import embedder

    embedder.clear_embedding_cache()
    monkeypatch.setattr(embedder.settings, "EMBED_PROVIDER", "openai")
    monkeypatch.setattr(embedder.settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(embedder.settings, "OPENAI_EMBED_MODEL", "text-embedding-3-small")
    monkeypatch.setattr(embedder.settings, "OPENAI_EMBED_DIM", 768)
    yield
    embedder.clear_embedding_cache()


def _mock_openai_response(count: int) -> MagicMock:
    data = []
    for i in range(count):
        vec = np.random.rand(768).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        data.append({"index": i, "embedding": vec.tolist()})
    response = MagicMock()
    response.json.return_value = {
        "data": data,
        "usage": {"prompt_tokens": 42},
    }
    response.raise_for_status.return_value = None
    return response


def test_embed_batch_returns_normalized_vectors(monkeypatch):
    from buzz_news import embedder

    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs["json"])
        return _mock_openai_response(len(kwargs["json"]["input"]))

    monkeypatch.setattr(embedder.httpx, "post", fake_post)

    texts = [
        "First test article about technology",
        "Second test article about sports",
    ]
    embeddings = embedder.embed_batch(texts)

    assert embeddings.shape == (2, 768)
    assert len(calls) == 1
    assert calls[0]["model"] == "text-embedding-3-small"
    assert calls[0]["dimensions"] == 768
    assert calls[0]["input"] == texts
    for i in range(len(texts)):
        norm = np.linalg.norm(embeddings[i])
        assert abs(norm - 1.0) < 1e-5


def test_embed_text_returns_single_normalized_vector(monkeypatch):
    from buzz_news import embedder

    monkeypatch.setattr(
        embedder.httpx,
        "post",
        lambda *args, **kwargs: _mock_openai_response(len(kwargs["json"]["input"])),
    )

    embedding = embedder.embed_text("A single test article")

    assert embedding.shape == (768,)
    norm = np.linalg.norm(embedding)
    assert abs(norm - 1.0) < 1e-5
