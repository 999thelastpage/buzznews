"""One-shot Phase 8.1 audit: per-item cosine distance to its own cluster centroid.

Reports:
  - distance distribution buckets across all items in last 7 days
  - count of items that would NOT attach at thresholds 0.20 / 0.18 / 0.15
  - top 20 clusters by mean intra-cluster distance (likely noisy)
  - sample titles from a few specific clusters known to be broken
  - new-cluster-count delta if we re-cluster from scratch at each threshold
"""
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select

from buzz_news.db import async_session_factory
from buzz_news.models import Cluster, RawItem


def cos_dist(a, b):
    return 1.0 - float(np.dot(a, b))


async def main() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    async with async_session_factory() as session:
        rows = (await session.execute(
            select(RawItem.id, RawItem.title, RawItem.cluster_id, RawItem.embedding, RawItem.fetched_at)
            .where(RawItem.embedding.isnot(None))
            .where(RawItem.cluster_id.isnot(None))
            .where(RawItem.fetched_at >= cutoff)
        )).all()
        clusters = {
            c.id: c
            for c in (await session.execute(
                select(Cluster).where(Cluster.centroid.isnot(None))
            )).scalars().all()
        }

    print(f"items in last 7d with embedding+cluster: {len(rows)}")
    print(f"clusters with centroid:                  {len(clusters)}")

    per_cluster_dists: dict[int, list[tuple[int, str, float]]] = defaultdict(list)
    all_dists: list[float] = []

    for r in rows:
        c = clusters.get(r.cluster_id)
        if c is None or c.centroid is None:
            continue
        emb = np.array(r.embedding, dtype=np.float32)
        cen = np.array(c.centroid, dtype=np.float32)
        emb_n = emb / (np.linalg.norm(emb) or 1.0)
        cen_n = cen / (np.linalg.norm(cen) or 1.0)
        d = cos_dist(emb_n, cen_n)
        per_cluster_dists[r.cluster_id].append((r.id, r.title or "", d))
        all_dists.append(d)

    if not all_dists:
        print("no data")
        return

    arr = np.array(all_dists)
    print()
    print("=== intra-cluster distance distribution (item to its OWN centroid) ===")
    print(f"  n={len(arr)}  mean={arr.mean():.3f}  p50={np.median(arr):.3f}  "
          f"p90={np.percentile(arr, 90):.3f}  p95={np.percentile(arr, 95):.3f}  "
          f"p99={np.percentile(arr, 99):.3f}  max={arr.max():.3f}")
    print()
    print("=== histogram (distance buckets) ===")
    edges = [0.0, 0.05, 0.10, 0.15, 0.18, 0.20, 0.25, 0.30, 0.50, 1.0, 2.01]
    for lo, hi in zip(edges, edges[1:]):
        n = int(((arr >= lo) & (arr < hi)).sum())
        bar = "#" * int(60 * n / len(arr))
        print(f"  [{lo:.2f}, {hi:.2f})  {n:5d}  {bar}")

    print()
    print("=== items that would NOT attach at tighter thresholds ===")
    for thr in (0.25, 0.20, 0.18, 0.15, 0.12):
        n_excl = int((arr >= thr).sum())
        pct = 100 * n_excl / len(arr)
        print(f"  threshold {thr:.2f}: {n_excl:5d} items detach ({pct:5.1f}%)")

    print()
    print("=== top 25 noisiest clusters (by mean intra-distance, n>=3) ===")
    cl_means = []
    for cid, items in per_cluster_dists.items():
        if len(items) < 3:
            continue
        ds = [d for _, _, d in items]
        cl_means.append((cid, float(np.mean(ds)), float(np.max(ds)), len(items)))
    cl_means.sort(key=lambda x: -x[1])

    for cid, m, mx, n in cl_means[:25]:
        print(f"  cluster {cid:5d}  n={n:3d}  mean={m:.3f}  max={mx:.3f}")

    print()
    print("=== sample titles from top 5 noisiest clusters ===")
    for cid, m, mx, n in cl_means[:5]:
        print(f"\n--- cluster {cid}  (n={n}, mean_dist={m:.3f}) ---")
        items_sorted = sorted(per_cluster_dists[cid], key=lambda x: x[2])
        for iid, title, d in items_sorted:
            tag = "  " if d < 0.18 else ("OK" if d < 0.20 else ("?" if d < 0.25 else "FAR"))
            print(f"  [{tag:3s}] dist={d:.3f}  id={iid:6d}  {title[:90]}")


if __name__ == "__main__":
    asyncio.run(main())
