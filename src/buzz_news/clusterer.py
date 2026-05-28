import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
from sqlalchemy import select, update, func, Integer, or_

from buzz_news.db import async_session_factory
from buzz_news.entities import entities_overlap, extract_entities
from buzz_news.models import RawItem, Cluster, Source, Article
from buzz_news.embedder import active_embedding_identity, embed_batch_with_usage, estimate_tokens
from buzz_news.embedding_budget import remaining_embedding_tokens, record_embedding_usage
from buzz_news.minhash import create_minhash, is_duplicate, MinHashLSH
from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.clusterer")

# Tighter than original 0.25 — Phase 8 audit showed the [0.20, 0.25) bucket was
# the modal attach distance and largely off-event ride-alongs.
COSINE_DISTANCE_THRESHOLD = 0.18
RECENT_WINDOW_HOURS = 48
CENTROID_EMA_ALPHA = 0.2
# Freeze the centroid after the cluster has this many items. Centroid drift is
# the main failure mode the audit surfaced — mega-clusters formed by repeated
# small drifts pulling in nearby-but-eventless items.
CENTROID_FREEZE_AFTER = 3
# Hard ceiling — a real news event cluster rarely needs more than ~20 items in
# 48h; beyond this the cluster is almost always a topic sink (e.g. cluster 598
# at 1838 items).
MAX_CLUSTER_SIZE = 25
MAX_MERGES_PER_SWEEP = 20
SIMILARITY_MERGE_THRESHOLD = 0.92


async def embed_unclustered_items() -> int:
    identity = active_embedding_identity()
    async with async_session_factory() as session:
        result = await session.execute(
            select(RawItem)
            .where(RawItem.cluster_id.is_(None))
            .where(RawItem.body.isnot(None) | RawItem.snippet.isnot(None))
            .where(
                or_(
                    RawItem.embedding.is_(None),
                    RawItem.embedding_provider.is_(None),
                    RawItem.embedding_provider != identity.provider,
                    RawItem.embedding_model != identity.model,
                    RawItem.embedding_dim != identity.dim,
                )
            )
            .order_by(RawItem.fetched_at.desc())
            .limit(500)
        )
        items = list(result.scalars().all())

    if not items:
        return 0

    remaining = await remaining_embedding_tokens(identity.provider, identity.model)
    if remaining <= 0:
        log.warning("Skipping raw embeddings: daily embedding budget is exhausted")
        return 0

    texts = []
    item_ids = []
    used_estimate = 0
    for item in items:
        title = item.title or ""
        body = (item.body or "")[:1000]
        snippet = (item.snippet or "")[:500]
        text = f"{title}. {body or snippet}"
        est = estimate_tokens(text)
        if used_estimate + est > remaining:
            continue
        texts.append(text)
        item_ids.append(item.id)
        used_estimate += est

    if not texts:
        log.warning("Skipping raw embeddings: no pending item fits remaining daily budget")
        return 0

    try:
        result = embed_batch_with_usage(texts)
    except Exception as e:
        log.error(f"Embedding failed: {e}")
        return 0

    await record_embedding_usage(result.usage, "RETRIEVAL_DOCUMENT")

    async with async_session_factory() as session:
        for item_id, embedding in zip(item_ids, result.vectors):
            await session.execute(
                update(RawItem)
                .where(RawItem.id == item_id)
                .values(
                    embedding=embedding.tolist(),
                    embedding_provider=identity.provider,
                    embedding_model=identity.model,
                    embedding_dim=identity.dim,
                )
            )
        await session.commit()

    return len(item_ids)


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    return 1.0 - np.dot(a, b)


def _normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    if norm > 0:
        return v / norm
    return v


async def _cluster_entities(cluster_id: int, session) -> set[str]:
    """Aggregate Latin-script entities across a cluster's existing items.

    Pulled lazily and cached by the caller for the duration of one attach
    decision — items already in a cluster don't change between attaches.
    """
    rows = (
        await session.execute(
            select(RawItem.title, RawItem.snippet)
            .where(RawItem.cluster_id == cluster_id)
            .limit(25)
        )
    ).all()
    ents: set[str] = set()
    for r in rows:
        ents |= extract_entities(r.title)
        ents |= extract_entities(r.snippet)
    return ents


async def _find_nearest_cluster(
    embedding: np.ndarray,
    candidate_entities: set[str],
    session,
) -> Optional[tuple[int, np.ndarray, int]]:
    """Find the closest unpublished cluster within threshold.

    Three new gates on top of the cosine threshold (Phase 8):
      - size cap: clusters at MAX_CLUSTER_SIZE never attract more items
      - NER overlap: if both candidate and cluster have Latin entities, require
        at least one shared entity (Hindi-only / entity-less items fall through
        to cosine-only)
      - returns the cluster's current source_count so the caller can decide
        whether to freeze the centroid

    Returns (cluster_id, centroid, source_count) or None.
    """
    result = await session.execute(
        _candidate_cluster_stmt(datetime.now(timezone.utc).replace(microsecond=0))
    )
    clusters = list(result.scalars().all())

    best_cluster_id = None
    best_distance = COSINE_DISTANCE_THRESHOLD
    best_centroid = None
    best_count = 0
    entity_cache: dict[int, set[str]] = {}

    for cluster in clusters:
        if cluster.centroid is None:
            continue
        if (cluster.source_count or 0) >= MAX_CLUSTER_SIZE:
            continue
        centroid = np.array(cluster.centroid, dtype=np.float32)
        dist = _cosine_distance(embedding, centroid)
        if dist >= best_distance:
            continue
        # NER gate: fires only when both sides have entities. Hindi-only items
        # and pure-topic clusters (no Latin tokens) fall through to cosine.
        if candidate_entities:
            if cluster.id not in entity_cache:
                entity_cache[cluster.id] = await _cluster_entities(cluster.id, session)
            cl_ents = entity_cache[cluster.id]
            if cl_ents and not entities_overlap(cl_ents, candidate_entities):
                continue
        best_distance = dist
        best_cluster_id = cluster.id
        best_centroid = centroid
        best_count = cluster.source_count or 0

    if best_cluster_id is not None:
        return (best_cluster_id, best_centroid, best_count)
    return None


def _candidate_cluster_stmt(now: datetime):
    """Clusters that may attract a new item.

    `is_published` is overloaded: a real published cluster has an Article row,
    while a sanity-sweep victim is also marked published as a tombstone. Allow
    the former to keep gathering source updates, but never resurrect the latter.
    """
    identity = active_embedding_identity()
    return (
        select(Cluster)
        .outerjoin(Article, Article.cluster_id == Cluster.id)
        .where(or_(Cluster.is_published == False, Article.id.isnot(None)))  # noqa: E712
        .where(Cluster.last_seen_at >= now - timedelta(hours=RECENT_WINDOW_HOURS))
        .where(Cluster.centroid_provider == identity.provider)
        .where(Cluster.centroid_model == identity.model)
        .where(Cluster.centroid_dim == identity.dim)
    )


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

    # Category recompute: pick the highest authority-weighted non-"general"
    # category from the cluster's sources. Catalog "general" is a catch-all
    # for NDTV / Google News / Aaj Tak that publish across topics; letting it
    # outvote a specialty source (Hindu Sport, BBC Tech) would defeat the
    # purpose. If no specialty source is attached, the existing category
    # (set at cluster creation from the first source) is kept.
    cat_row = (
        await session.execute(
            select(
                Source.category,
                func.sum(Source.authority).label("w"),
            )
            .select_from(RawItem)
            .join(Source, RawItem.source_id == Source.id)
            .where(RawItem.cluster_id == cluster_id)
            .where(Source.category != "general")
            .group_by(Source.category)
            .order_by(func.sum(Source.authority).desc())
            .limit(1)
        )
    ).fetchone()
    if cat_row:
        await session.execute(
            update(Cluster)
            .where(Cluster.id == cluster_id)
            .values(category=cat_row.category)
        )


async def run_once() -> int:
    identity = active_embedding_identity()
    async with async_session_factory() as session:
        result = await session.execute(
            select(RawItem)
            .where(RawItem.embedding.isnot(None))
            .where(RawItem.embedding_provider == identity.provider)
            .where(RawItem.embedding_model == identity.model)
            .where(RawItem.embedding_dim == identity.dim)
            .where(RawItem.cluster_id.is_(None))
            .limit(500)
        )
        items = list(result.scalars().all())

    if not items:
        return 0

    lsh = MinHashLSH(threshold=0.85, num_perm=128)
    minhashes: dict[str, tuple[int, object]] = {}

    # Insert + query must use identical text composition or Jaccard drifts
    # below the 0.85 threshold even on byte-identical bodies (8 GDELT copies
    # of the same Hyundai recall slipped through previously because INSERT
    # used body-only and QUERY used title+body). Items are added to the LSH
    # AFTER they are processed, so each item never matches itself.
    def _dedup_text(it) -> str:
        return f"{it.title}. {it.body or it.snippet or ''}"

    clustered_count = 0
    now = datetime.now(timezone.utc)

    def _register_in_lsh(item) -> None:
        if not (item.body or item.snippet):
            return
        m = create_minhash(_dedup_text(item))
        key = str(item.id)
        if key in minhashes:
            return
        lsh.insert(key, m)
        minhashes[key] = (item.id, m)

    for item in items:
        text = _dedup_text(item)
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
                    _register_in_lsh(item)
                    continue

        embedding = np.array(item.embedding, dtype=np.float32)
        candidate_entities = extract_entities(item.title) | extract_entities(item.snippet)

        async with async_session_factory() as session:
            nearest = await _find_nearest_cluster(embedding, candidate_entities, session)

            if nearest is not None:
                cluster_id, old_centroid, current_count = nearest
                # Freeze centroid past the first few items so a cluster's anchor
                # event can't drift into a topic sink (Phase 8 audit finding).
                if current_count < CENTROID_FREEZE_AFTER:
                    new_centroid = CENTROID_EMA_ALPHA * embedding + (1 - CENTROID_EMA_ALPHA) * old_centroid
                    new_centroid = _normalize(new_centroid)
                    centroid_update = new_centroid.tolist()
                else:
                    centroid_update = old_centroid.tolist()

                await session.execute(
                    update(Cluster)
                    .where(Cluster.id == cluster_id)
                    .values(
                        centroid=centroid_update,
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
                identity = active_embedding_identity()
                new_cluster = Cluster(
                    centroid=embedding.tolist(),
                    centroid_provider=identity.provider,
                    centroid_model=identity.model,
                    centroid_dim=identity.dim,
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

        _register_in_lsh(item)

    log.info(f"Clustered {clustered_count} items")
    return clustered_count


async def sanity_sweep() -> int:
    merged = 0
    async with async_session_factory() as session:
        identity = active_embedding_identity()
        result = await session.execute(
            select(Cluster)
            .where(Cluster.centroid.isnot(None))
            .where(Cluster.centroid_provider == identity.provider)
            .where(Cluster.centroid_model == identity.model)
            .where(Cluster.centroid_dim == identity.dim)
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
