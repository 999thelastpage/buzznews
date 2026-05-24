import pytest
from unittest.mock import patch, MagicMock
import numpy as np


@pytest.fixture(autouse=True)
def mock_genai():
    mock_embedding = np.random.rand(768).astype(np.float32)
    mock_embedding = mock_embedding / np.linalg.norm(mock_embedding)

    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=mock_embedding.tolist())]

    mock_client = MagicMock()
    mock_client.models.embed_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client):
        yield


def test_embed_batch_returns_normalized_vectors():
    from buzz_news.embedder import embed_batch
    texts = [
        "First test article about technology",
        "Second test article about sports",
    ]
    embeddings = embed_batch(texts)

    assert embeddings.shape[0] == 2
    assert embeddings.shape[1] == 768
    for i in range(len(texts)):
        norm = np.linalg.norm(embeddings[i])
        assert abs(norm - 1.0) < 1e-5


def test_embed_text_returns_single_normalized_vector():
    from buzz_news.embedder import embed_text
    embedding = embed_text("A single test article")

    assert embedding.shape == (768,)
    norm = np.linalg.norm(embedding)
    assert abs(norm - 1.0) < 1e-5
