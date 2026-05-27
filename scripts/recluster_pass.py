"""Phase 8.3 re-cluster pass: tighten existing bloated unpublished clusters.

For each unpublished cluster with >= 5 items:
  1. Recompute the centroid as the L2-normalized mean of the first
     CENTROID_FREEZE_AFTER items (the "core" event), restoring the pre-drift
     centroid that should never have moved.
  2. Detach any item with distance-to-core > DETACH_THRESHOLD by setting
     RawItem.cluster_id = NULL. Next `cluster-once` pass will re-attach them
     correctly under the tightened threshold + size cap + NER gate.

Does NOT touch published clusters (those have an Article row bound to them and
their state is canonical). Does NOT mutate Article rows.

Idempotent — safe to re-run.
"""
import argparse
import asyncio
import logging
from collections import defaultdict

import numpy as np
from sqlalchemy import select, update

from buzz_news.clusterer import CENTROID_FREEZE_AFTER, _cosine_distance, _normalize
from buzz_news.db import async_session_factory
from buzz_news.models import Cluster, RawItem

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("recluster")

# Slightly looser than the new attach threshold (0.18). We want to leave the
# core+drift band intact but evict the obvious off-event items. A re-cluster
# pass at exactly 0.18 would shred legitimate slow-developing stories.
DETACH_THRESHOLD = 0.22
MIN_CLUSTER_SIZE = 5


async def main(dry_run: bool = False) -> None:
    if dry_run:
        log.info("DRY RUN — no DB writes")
    async with async_session_factory() as session:
        clusters = list(
            (
                await session.execute(
                    select(Cluster)
                    .where(Cluster.is_published == False)  # noqa: E712
                    .where(Cluster.centroid.isnot(None))
                )
            ).scalars().all()
        )
        rows = (
            await session.execute(
                select(RawItem.id, RawItem.cluster_id, RawItem.embedding, RawItem.fetched_at)
                .where(RawItem.cluster_id.isnot(None))
                .where(RawItem.embedding.isnot(None))
            )
        ).all()

    by_cluster: dict[int, list] = defaultdict(list)
    for r in rows:
        by_cluster[r.cluster_id].append(r)

    touched_clusters = 0
    detached_items = 0

    for cluster in clusters:
        items = by_cluster.get(cluster.id, [])
        if len(items) < MIN_CLUSTER_SIZE:
            continue
        items_sorted = sorted(items, key=lambda x: x.fetched_at)
        core = items_sorted[:CENTROID_FREEZE_AFTER]
        core_embs = np.array([it.embedding for it in core], dtype=np.float32)
        mean = core_embs.mean(axis=0)
        true_centroid = _normalize(mean)

        to_detach: list[int] = []
        for it in items_sorted:
            emb = np.array(it.embedding, dtype=np.float32)
            emb_n = emb / (np.linalg.norm(emb) or 1.0)
            d = _cosine_distance(emb_n, true_centroid)
            if d > DETACH_THRESHOLD:
                to_detach.append(it.id)

        if to_detach:
            if not dry_run:
                async with async_session_factory() as session:
                    await session.execute(
                        update(RawItem)
                        .where(RawItem.id.in_(to_detach))
                        .values(cluster_id=None)
                    )
                    await session.execute(
                        update(Cluster)
                        .where(Cluster.id == cluster.id)
                        .values(centroid=true_centroid.tolist())
                    )
                    await session.commit()
            log.info(
                f"cluster {cluster.id}: {'WOULD detach' if dry_run else 'detached'} "
                f"{len(to_detach)}/{len(items)} (recentered on first {len(core)})"
            )
            touched_clusters += 1
            detached_items += len(to_detach)

    log.info(
        f"done: {'would touch' if dry_run else 'touched'} {touched_clusters} clusters, "
        f"{'would detach' if dry_run else 'detached'} {detached_items} items"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report impact without mutating DB")
    args = ap.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
