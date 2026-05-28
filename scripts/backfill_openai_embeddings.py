"""Backfill active embeddings to the configured provider/model.

Run after switching EMBED_PROVIDER=openai. It prioritizes published articles
and recent raw items, then recomputes centroids for touched clusters from
active-provider raw item vectors. The daily embedding token cap is honored unless --ignore-budget is passed.

    /home/ubuntu/buzznews/.venv/bin/python scripts/backfill_openai_embeddings.py --hours 72
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from sqlalchemy import func, select, update

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from buzz_news.db import async_session_factory  # noqa: E402
from buzz_news.embedder import active_embedding_identity, embed_batch_with_usage, estimate_tokens  # noqa: E402
from buzz_news.embedding_budget import remaining_embedding_tokens, record_embedding_usage  # noqa: E402
from buzz_news.models import Article, Cluster, RawItem  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("backfill_openai_embeddings")

BATCH = 50


def _normalize(vec: np.ndarray) -> list[float]:
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


async def _embed_rows(rows, text_fn, update_fn, task_type: str, *, ignore_budget: bool = False) -> tuple[int, set[int]]:
    identity = active_embedding_identity()
    total = 0
    touched_clusters: set[int] = set()
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        remaining = 2**31 - 1 if ignore_budget else await remaining_embedding_tokens(identity.provider, identity.model)
        if remaining <= 0:
            log.warning("Daily embedding budget exhausted; stopping backfill")
            break

        selected = []
        texts = []
        used = 0
        for row in chunk:
            text = text_fn(row)
            est = estimate_tokens(text)
            if used + est > remaining:
                continue
            selected.append(row)
            texts.append(text)
            used += est

        if not selected:
            continue

        result = embed_batch_with_usage(texts, task_type=task_type)
        await record_embedding_usage(result.usage, task_type)

        async with async_session_factory() as session:
            for row, vec in zip(selected, result.vectors):
                await update_fn(session, row, vec, identity)
                if getattr(row, "cluster_id", None):
                    touched_clusters.add(row.cluster_id)
            await session.commit()

        total += len(selected)
        log.info("embedded %d/%d rows for %s", total, len(rows), task_type)
    return total, touched_clusters


async def _backfill_articles(*, ignore_budget: bool = False) -> int:
    identity = active_embedding_identity()
    async with async_session_factory() as session:
        rows = (await session.execute(
            select(Article)
            .where(
                (Article.embedding.is_(None))
                | (Article.embedding_provider.is_(None))
                | (Article.embedding_provider != identity.provider)
                | (Article.embedding_model != identity.model)
                | (Article.embedding_dim != identity.dim)
            )
            .order_by(Article.published_at.desc())
        )).scalars().all()

    async def update_article(session, row, vec, ident):
        await session.execute(
            update(Article)
            .where(Article.id == row.id)
            .values(
                embedding=vec.tolist(),
                embedding_provider=ident.provider,
                embedding_model=ident.model,
                embedding_dim=ident.dim,
            )
        )

    count, _ = await _embed_rows(
        list(rows),
        lambda r: f"{r.title_en}\n{r.summary_en or ''}",
        update_article,
        "RETRIEVAL_DOCUMENT",
        ignore_budget=ignore_budget,
    )
    return count


async def _backfill_raw(hours: int, *, ignore_budget: bool = False) -> set[int]:
    identity = active_embedding_identity()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    async with async_session_factory() as session:
        rows = (await session.execute(
            select(RawItem)
            .where(RawItem.fetched_at >= cutoff)
            .where(RawItem.body.isnot(None) | RawItem.snippet.isnot(None))
            .where(
                (RawItem.embedding.is_(None))
                | (RawItem.embedding_provider.is_(None))
                | (RawItem.embedding_provider != identity.provider)
                | (RawItem.embedding_model != identity.model)
                | (RawItem.embedding_dim != identity.dim)
            )
            .order_by(RawItem.fetched_at.desc())
        )).scalars().all()

    async def update_raw(session, row, vec, ident):
        await session.execute(
            update(RawItem)
            .where(RawItem.id == row.id)
            .values(
                embedding=vec.tolist(),
                embedding_provider=ident.provider,
                embedding_model=ident.model,
                embedding_dim=ident.dim,
            )
        )

    count, touched = await _embed_rows(
        list(rows),
        lambda r: f"{r.title or ''}. {(r.body or '')[:1000] or (r.snippet or '')[:500]}",
        update_raw,
        "RETRIEVAL_DOCUMENT",
        ignore_budget=ignore_budget,
    )
    log.info("raw backfill embedded %d rows", count)
    return touched


async def _recompute_centroids(cluster_ids: set[int]) -> int:
    if not cluster_ids:
        return 0
    identity = active_embedding_identity()
    updated = 0
    async with async_session_factory() as session:
        for cluster_id in sorted(cluster_ids):
            rows = (await session.execute(
                select(RawItem.embedding)
                .where(RawItem.cluster_id == cluster_id)
                .where(RawItem.embedding.isnot(None))
                .where(RawItem.embedding_provider == identity.provider)
                .where(RawItem.embedding_model == identity.model)
                .where(RawItem.embedding_dim == identity.dim)
            )).scalars().all()
            if not rows:
                continue
            centroid = _normalize(np.mean(np.array(rows, dtype=np.float32), axis=0))
            await session.execute(
                update(Cluster)
                .where(Cluster.id == cluster_id)
                .values(
                    centroid=centroid,
                    centroid_provider=identity.provider,
                    centroid_model=identity.model,
                    centroid_dim=identity.dim,
                    source_count=func.coalesce(Cluster.source_count, 0),
                )
            )
            updated += 1
        await session.commit()
    return updated


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--ignore-budget", action="store_true", help="Run this backfill even if today's embedding cap is exhausted.")
    args = parser.parse_args()

    identity = active_embedding_identity()
    log.info(
        "Backfilling provider=%s model=%s dim=%d ignore_budget=%s",
        identity.provider,
        identity.model,
        identity.dim,
        args.ignore_budget,
    )
    articles = await _backfill_articles(ignore_budget=args.ignore_budget)
    touched = await _backfill_raw(args.hours, ignore_budget=args.ignore_budget)
    centroids = await _recompute_centroids(touched)
    log.info("Done: articles=%d touched_clusters=%d centroids=%d", articles, len(touched), centroids)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
