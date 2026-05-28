"""One-shot backfill of Article.embedding for rows that don't have one yet.

Idempotent: only embeds rows WHERE embedding IS NULL. Run as ubuntu so the
DB connection uses the same credentials the worker does:

    sudo -u ubuntu /home/ubuntu/buzznews/.venv/bin/python \\
        scripts/backfill_article_embeddings.py
"""
import asyncio
import logging
import sys
from pathlib import Path

# Make `buzz_news` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import select, update  # noqa: E402

from buzz_news.db import async_session_factory  # noqa: E402
from buzz_news.embedder import active_embedding_identity, embed_batch_with_usage  # noqa: E402
from buzz_news.embedding_budget import record_embedding_usage  # noqa: E402
from buzz_news.models import Article  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("backfill_embeddings")

BATCH = 50


async def main() -> int:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Article.id, Article.title_en, Article.summary_en)
            .where(Article.embedding.is_(None))
            .order_by(Article.id)
        )
        rows = result.fetchall()

    if not rows:
        log.info("No articles need embeddings. Nothing to do.")
        return 0

    log.info(f"Backfilling embeddings for {len(rows)} articles in batches of {BATCH}")

    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        texts = [f"{r.title_en}\n{r.summary_en or ''}" for r in chunk]
        try:
            identity = active_embedding_identity()
            result = embed_batch_with_usage(texts, task_type="RETRIEVAL_DOCUMENT")
            await record_embedding_usage(result.usage, "RETRIEVAL_DOCUMENT")
        except Exception as e:
            log.error(f"embed_batch failed for chunk starting at {i}: {e}")
            continue

        async with async_session_factory() as session:
            for row, vec in zip(chunk, result.vectors):
                await session.execute(
                    update(Article)
                    .where(Article.id == row.id)
                    .values(
                        embedding=vec.tolist(),
                        embedding_provider=identity.provider,
                        embedding_model=identity.model,
                        embedding_dim=identity.dim,
                    )
                )
            await session.commit()

        total += len(chunk)
        log.info(f"  embedded {total}/{len(rows)}")

    log.info(f"Done. Embedded {total} articles.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
