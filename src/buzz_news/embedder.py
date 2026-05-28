import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass

import httpx
import numpy as np
from tenacity import retry, stop_after_attempt, wait_exponential

from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.embedder")

BATCH_SIZE = 100
EMBED_CACHE_SIZE = 2000
OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"


@dataclass(frozen=True)
class EmbeddingIdentity:
    provider: str
    model: str
    dim: int


@dataclass(frozen=True)
class EmbeddingUsage:
    provider: str
    model: str
    input_tokens: int
    requests: int
    item_count: int


@dataclass(frozen=True)
class EmbeddingBatchResult:
    vectors: np.ndarray
    usage: EmbeddingUsage


_cache: OrderedDict[tuple[str, str, str, str], np.ndarray] = OrderedDict()


def active_embedding_identity() -> EmbeddingIdentity:
    provider = (settings.EMBED_PROVIDER or "gemini").strip().lower()
    if provider == "openai":
        return EmbeddingIdentity("openai", settings.OPENAI_EMBED_MODEL, settings.OPENAI_EMBED_DIM)
    if provider == "gemini":
        return EmbeddingIdentity("gemini", settings.GEMINI_MODEL_EMBED, settings.EMBED_DIM)
    raise ValueError(f"Unsupported EMBED_PROVIDER={settings.EMBED_PROVIDER!r}")


def estimate_tokens(text: str) -> int:
    # Conservative for mixed English/Hindi without adding tokenizer deps.
    return max(1, (len(text or "") + 2) // 3)


def estimate_batch_tokens(texts: list[str]) -> int:
    return sum(estimate_tokens(t[:8000] if t else "") for t in texts)


def clear_embedding_cache() -> None:
    _cache.clear()


def _cache_key(identity: EmbeddingIdentity, task_type: str, text: str) -> tuple[str, str, str, str]:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return (identity.provider, identity.model, task_type, h)


def _cache_get(key: tuple[str, str, str, str]) -> np.ndarray | None:
    value = _cache.get(key)
    if value is None:
        return None
    _cache.move_to_end(key)
    return value.copy()


def _cache_put(key: tuple[str, str, str, str], value: np.ndarray) -> None:
    _cache[key] = value.copy()
    _cache.move_to_end(key)
    while len(_cache) > EMBED_CACHE_SIZE:
        _cache.popitem(last=False)


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def _embed_openai(texts: list[str], identity: EmbeddingIdentity) -> tuple[np.ndarray, int]:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured")
    response = httpx.post(
        OPENAI_EMBED_URL,
        headers={
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": identity.model,
            "input": texts,
            "dimensions": identity.dim,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()
    data = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
    if len(data) != len(texts):
        raise RuntimeError(f"OpenAI returned {len(data)} embeddings for {len(texts)} inputs")
    vectors = np.array([item["embedding"] for item in data], dtype=np.float32)
    tokens = int((payload.get("usage") or {}).get("prompt_tokens") or estimate_batch_tokens(texts))
    return _normalize(vectors), tokens


def _embed_gemini(texts: list[str], task_type: str, identity: EmbeddingIdentity) -> tuple[np.ndarray, int]:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not configured")
    from google import genai

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    config = {"task_type": task_type, "output_dimensionality": identity.dim}
    response = client.models.embed_content(
        model=identity.model,
        contents=texts,
        config=config,
    )
    vectors = np.array([emb.values for emb in response.embeddings], dtype=np.float32)
    return _normalize(vectors), estimate_batch_tokens(texts)


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, max=60))
def _embed_missing(texts: list[str], task_type: str, identity: EmbeddingIdentity) -> tuple[np.ndarray, int, int]:
    input_tokens = 0
    requests = 0
    vectors = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        if identity.provider == "openai":
            batch_vectors, batch_tokens = _embed_openai(batch, identity)
        else:
            batch_vectors, batch_tokens = _embed_gemini(batch, task_type, identity)
        vectors.extend(batch_vectors)
        input_tokens += batch_tokens
        requests += 1
    return np.vstack(vectors), input_tokens, requests


def embed_batch_with_usage(
    texts: list[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> EmbeddingBatchResult:
    identity = active_embedding_identity()
    texts = [t[:8000] if t else "" for t in texts]
    if not texts:
        return EmbeddingBatchResult(
            vectors=np.empty((0, identity.dim), dtype=np.float32),
            usage=EmbeddingUsage(identity.provider, identity.model, 0, 0, 0),
        )

    output: list[np.ndarray | None] = [None] * len(texts)
    missing_texts: list[str] = []
    missing_keys: list[tuple[str, str, str, str]] = []
    missing_positions: list[int] = []

    for pos, text in enumerate(texts):
        key = _cache_key(identity, task_type, text)
        cached = _cache_get(key)
        if cached is not None:
            output[pos] = cached
            continue
        missing_texts.append(text)
        missing_keys.append(key)
        missing_positions.append(pos)

    input_tokens = 0
    requests = 0
    if missing_texts:
        try:
            missing_vectors, input_tokens, requests = _embed_missing(missing_texts, task_type, identity)
        except Exception as e:
            log.error(f"Embedding batch failed after retries: {e}")
            raise
        for pos, key, vector in zip(missing_positions, missing_keys, missing_vectors):
            _cache_put(key, vector)
            output[pos] = vector

    vectors = np.vstack([v for v in output if v is not None])
    usage = EmbeddingUsage(
        provider=identity.provider,
        model=identity.model,
        input_tokens=input_tokens,
        requests=requests,
        item_count=len(missing_texts),
    )
    log.info(
        "EMBED_USAGE provider=%s model=%s task_type=%s items=%d requests=%d input_tokens=%d",
        usage.provider,
        usage.model,
        task_type,
        usage.item_count,
        usage.requests,
        usage.input_tokens,
    )
    return EmbeddingBatchResult(vectors=vectors, usage=usage)


def embed_batch(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> np.ndarray:
    return embed_batch_with_usage(texts, task_type).vectors


def embed_text(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> np.ndarray:
    return embed_batch([text], task_type=task_type)[0]
