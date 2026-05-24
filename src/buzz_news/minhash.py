import logging
from datasketch import MinHash, MinHashLSH

from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.minhash")

JACCARD_THRESHOLD = 0.85
NUM_PERM = 128


def create_minhash(text: str, num_perm: int = NUM_PERM) -> MinHash:
    m = MinHash(num_perm=num_perm)
    words = text.lower().split()
    for word in words:
        if len(word) >= 4:
            m.update(word.encode("utf8"))
    return m


def is_duplicate(
    text: str,
    lsh: MinHashLSH,
    minhashes: dict[str, MinHash],
    threshold: float = JACCARD_THRESHOLD,
) -> str | None:
    m = create_minhash(text)
    for bucket_key in lsh.query(m):
        existing = minhashes.get(bucket_key)
        if existing is None:
            continue
        jaccard = m.jaccard(existing)
        if jaccard >= threshold:
            return bucket_key
    return None


def deduplicate_texts(
    texts: list[str],
    ids: list[str],
    threshold: float = JACCARD_THRESHOLD,
) -> list[list[str]]:
    lsh = MinHashLSH(threshold=threshold, num_perm=NUM_PERM)
    minhashes: dict[str, MinHash] = {}
    clusters: dict[str, list[str]] = {}

    for text, item_id in zip(texts, ids):
        m = create_minhash(text)
        dup_key = is_duplicate(text, lsh, minhashes, threshold)
        if dup_key is not None:
            clusters[dup_key].append(item_id)
        else:
            lsh.insert(item_id, m)
            minhashes[item_id] = m
            clusters[item_id] = [item_id]

    return list(clusters.values())
