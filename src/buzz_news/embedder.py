import logging
import hashlib
from functools import lru_cache

import numpy as np
from tenacity import retry, stop_after_attempt, wait_exponential

from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.embedder")

BATCH_SIZE = 100
EMBED_CACHE_SIZE = 2000


@lru_cache(maxsize=EMBED_CACHE_SIZE)
def _cached_embedding(text_hash: str, text: str, task_type: str) -> np.ndarray:
    from google import genai
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    # output_dimensionality must be set for ALL task types or query/document
    # vectors come back different shapes (3072 vs 768) and break comparison.
    config = {"task_type": task_type, "output_dimensionality": 768}
    response = client.models.embed_content(
        model=settings.GEMINI_MODEL_EMBED,
        contents=[text[:8000]],
        config=config,
    )
    values = response.embeddings[0].values
    embedding = np.array(values, dtype=np.float32)
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm
    return embedding


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, max=60))
def embed_batch(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> np.ndarray:
    texts = [t[:8000] if t else "" for t in texts]
    embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        batch_hashes = [hashlib.sha1(t.encode()).hexdigest() for t in batch]
        try:
            cached = [_cached_embedding(h, t, task_type) for h, t in zip(batch_hashes, batch)]
            embeddings.extend(cached)
        except Exception as e:
            log.error(f"Embedding batch failed after retries: {e}")
            raise
    return np.vstack(embeddings)


def embed_text(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> np.ndarray:
    h = hashlib.sha1(text.encode()).hexdigest()
    return _cached_embedding(h, text[:8000], task_type)
