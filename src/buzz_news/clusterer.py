import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
from sqlalchemy import select, update, func, Integer

from buzz_news.db import async_session_factory
from buzz_news.models import RawItem, Cluster, Source
from buzz_news.embedder import embed_batch
from buzz_news.minhash import create_minhash, is_duplicate, MinHashLSH
from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.clusterer")

COSINE_DISTANCE_THRESHOLD = 0.25
RECENT_WINDOW_HOURS = 48
CENTROID_EMA_ALPHA = 0.2
MAX_MERGES_PER_SWEEP = 20
SIMILARITY_MERGE_THRESHOLD = 0.92


async def embed_unclustered_items() -> int:
    async with async_session_factory() as session:
        result = await session.execute(
            select(RawItem)
            .where(RawItem.embedding.is_(None))
            .where(RawItem.body.isnot(None) | RawItem.snippet.isnot(None))
            .limit(500)
        )
        items = list(result.scalars().all())

    if not items:
        return 0

    texts = []
    item_ids = []
    for item in items:
        title = item.title or ""
        body = (item.body or "")[:1000]
        snippet = (item.snippet or "")[:500]
        text = f"{title}. {body or snippet}"
        texts.append(text)
        item_ids.append(item.id)

    try:
        embeddings = embed_batch(texts)
    except Exception as e:
        log.error(f"Embedding failed: {e}")
        return 0

    async with async_session_factory() as session:
        for item_id, embedding in zip(item_ids, embeddings):
            await session.execute(
                update(RawItem)
                .where(RawItem.id == item_id)
                .values(embedding=embedding.tolist())
            )
        await session.commit()

    return len(items)


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    return 1.0 - np.dot(a, b)


def _normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    if norm > 0:
        return v / norm
    return v


async def _find_nearest_cluster(
    embedding: np.ndarray,
    session,
) -> Optional[tuple[int, np.ndarray]]:
    result = await session.execute(
        select(Cluster)
        .where(Cluster.is_published == False)  # noqa: E712
        .where(
            Cluster.last_seen_at
            >= datetime.now(timezone.utc).replace(microsecond=0)
            - timedelta(hours=RECENT_WINDOW_HOURS)
        )
    )
    clusters = list(result.scalars().all())

    best_cluster_id = None
    best_distance = COSINE_DISTANCE_THRESHOLD
    best_centroid = None

    for cluster in clusters:
        if cluster.centroid is None:
            continue
        centroid = np.array(cluster.centroid, dtype=np.float32)
        dist = _cosine_distance(embedding, centroid)
        if dist < best_distance:
            best_distance = dist
            best_cluster_id = cluster.id
            best_centroid = centroid

    if best_cluster_id is not None:
        return (best_cluster_id, best_centroid)
    return None


async def _update_cluster_counters(cluster_id: int, session) -> None:
    exec_result = session.execute(
        select(
            func.count(RawItem.id).label("source_count"),
            func.count(func.distinct(RawItem.source_id)).label("distinct_sources"),
            func.sum(Source.authority).label("authority_sum"),
            func.sum(func.cast(Source.is_tabloid, Integer)).label("tabloid_count"),
        )
        .select_from(RawItem)
        .join(Source, RawItem.source_id == Source.id)
        .where(RawItem.cluster_id == cluster_id)
    )
    result = await exec_result
    row = result.fetchone()

    source_count = row.source_count or 0
    distinct_sources = row.distinct_sources or 0
    authority_sum = float(row.authority_sum or 0)
    tabloid_count = row.tabloid_count or 0

    await session.execute(
        update(Cluster)
        .where(Cluster.id == cluster_id)
        .values(
            source_count=source_count,
            distinct_sources=distinct_sources,
            authority_sum=authority_sum,
            tabloid_count=tabloid_count,
            last_seen_at=datetime.now(timezone.utc),
        )
    )


async def run_once() -> int:
    async with async_session_factory() as session:
        result = await session.execute(
            select(RawItem)
            .where(RawItem.embedding.isnot(None))
            .where(RawItem.cluster_id.is_(None))
            .limit(500)
        )
        items = list(result.scalars().all())

    if not items:
        return 0

    lsh = MinHashLSH(threshold=0.85, num_perm=128)
    minhashes: dict[str, tuple[int, object]] = {}

    for item in items:
        if item.body:
            m = create_minhash(item.body)
            key = str(item.id)
            lsh.insert(key, m)
            minhashes[key] = (item.id, m)

    clustered_count = 0
    now = datetime.now(timezone.utc)

    for item in items:
        text = f"{item.title}. {item.body or item.snippet or ''}"
        dup_key = is_duplicate(text, lsh, {k: v[1] for k, v in minhashes.items()})
        if dup_key is not None:
            dup_item_id = minhashes[dup_key][0]
            async with async_session_factory() as session:
                result = await session.execute(
                    select(RawItem.cluster_id).where(RawItem.id == dup_item_id)
                )
                existing_cluster_id = result.scalar_one_or_none()
                if existing_cluster_id:
                    await session.execute(
                        update(RawItem)
                        .where(RawItem.id == item.id)
                        .values(cluster_id=existing_cluster_id)
                    )
                    await session.commit()
                    clustered_count += 1
                    log.debug(f"MinHash dedup: item {item.id} attached to cluster {existing_cluster_id}")
                    continue

        embedding = np.array(item.embedding, dtype=np.float32)

        async with async_session_factory() as session:
            nearest = await _find_nearest_cluster(embedding, session)

            if nearest is not None:
                cluster_id, old_centroid = nearest
                new_centroid = CENTROID_EMA_ALPHA * embedding + (1 - CENTROID_EMA_ALPHA) * old_centroid
                new_centroid = _normalize(new_centroid)

                await session.execute(
                    update(Cluster)
                    .where(Cluster.id == cluster_id)
                    .values(
                        centroid=new_centroid.tolist(),
                        last_seen_at=now,
                    )
                )
                await session.execute(
                    update(RawItem)
                    .where(RawItem.id == item.id)
                    .values(cluster_id=cluster_id)
                )
                await _update_cluster_counters(cluster_id, session)
                await session.commit()

                clustered_count += 1
                log.debug(f"Item {item.id} attached to cluster {cluster_id}")
            else:
                new_cluster = Cluster(
                    centroid=embedding.tolist(),
                    first_seen_at=now,
                    last_seen_at=now,
                    source_count=1,
                    distinct_sources=1,
                    primary_language=item.language,
                )
                async with async_session_factory() as sess:
                    sess.add(new_cluster)
                    await sess.flush()
                    cluster_id = new_cluster.id

                    await sess.execute(
                        update(RawItem)
                        .where(RawItem.id == item.id)
                        .values(cluster_id=cluster_id)
                    )

                    result = await sess.execute(select(Source).where(Source.id == item.source_id))
                    source = result.scalar_one_or_none()
                    authority = float(source.authority) if source else 0.5

                    await sess.execute(
                        update(Cluster)
                        .where(Cluster.id == cluster_id)
                        .values(
                            authority_sum=authority,
                            category=source.category if source else None,
                            region=source.region if source else None,
                        )
                    )
                    await sess.commit()

                clustered_count += 1
                log.debug(f"Created new cluster {cluster_id} for item {item.id}")

    log.info(f"Clustered {clustered_count} items")
    return clustered_count


async def sanity_sweep() -> int:
    merged = 0
    async with async_session_factory() as session:
        result = await session.execute(
            select(Cluster).where(Cluster.centroid.isnot(None))
        )
        clusters = list(result.scalars().all())

    if len(clusters) < 2:
        return 0

    centroids = [np.array(c.centroid, dtype=np.float32) for c in clusters]

    merges = []
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            sim = np.dot(centroids[i], centroids[j])
            if sim > SIMILARITY_MERGE_THRESHOLD:
                c1_size = clusters[i].source_count or 0
                c2_size = clusters[j].source_count or 0
                survivor = clusters[i].id if c1_size >= c2_size else clusters[j].id
                victim = clusters[j].id if c1_size >= c2_size else clusters[i].id
                merges.append((survivor, victim, sim))
                if len(merges) >= MAX_MERGES_PER_SWEEP:
                    break
        if len(merges) >= MAX_MERGES_PER_SWEEP:
            break

    for survivor_id, victim_id, sim in merges:
        async with async_session_factory() as session:
            await session.execute(
                update(RawItem)
                .where(RawItem.cluster_id == victim_id)
                .values(cluster_id=survivor_id)
            )
            await session.execute(
                update(Cluster)
                .where(Cluster.id == victim_id)
                .values(is_published=True)
            )
            await _update_cluster_counters(survivor_id, session)
            await session.commit()
            merged += 1
            log.info(f"Sanity sweep: merged cluster {victim_id} into {survivor_id} (sim={sim:.3f})")

    return merged


async def split_cluster(source_cluster_id: int, item_ids: list[int]) -> int:
    if not item_ids:
        return 0

    async with async_session_factory() as session:
        result = await session.execute(
            select(RawItem).where(RawItem.cluster_id == source_cluster_id).where(RawItem.id.in_(item_ids))
        )
        items = list(result.scalars().all())

    if not items:
        return 0

    new_cluster = Cluster(
        centroid=None,
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    async with async_session_factory() as sess:
        sess.add(new_cluster)
        await sess.flush()
        new_id = new_cluster.id
        await sess.commit()

    for item in items:
        async with async_session_factory() as sess:
            await sess.execute(
                update(RawItem)
                .where(RawItem.id == item.id)
                .values(cluster_id=new_id)
            )
            await sess.commit()

    async with async_session_factory() as sess:
        await _update_cluster_counters(source_cluster_id, sess)
        await _update_cluster_counters(new_id, sess)

    log.info(f"Split {len(items)} items from cluster {source_cluster_id} into new cluster {new_id}")
    return len(items)
