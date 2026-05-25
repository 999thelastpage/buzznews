"""article-level embeddings + FTS columns + search query cache

Revision ID: 0003_article_search
Revises: 0002_pause_source
Create Date: 2026-05-26

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0003_article_search"
down_revision: Union[str, Sequence[str], None] = "0002_pause_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This deployment bootstrapped via Base.metadata.create_all() instead of
    # alembic, so 0001's extension creation may have been skipped. Defensive.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Article-level pgvector embedding for hybrid search. Distinct from
    # raw_items.embedding (ARRAY(DOUBLE_PRECISION), Python-side cosine).
    # This one uses pgvector's native type so we can build an HNSW index.
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS embedding vector(768)")

    # FTS column generated from titles + bodies in both languages.
    # 'simple' config because Hindi (Devanagari) has no stemmer in default
    # PG configs; weights A/B let title hits outrank body hits at query time.
    op.execute("""
        ALTER TABLE articles ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('simple', coalesce(title_en, '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(title_hi, '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(summary_en, '')), 'B') ||
            setweight(to_tsvector('simple', coalesce(summary_hi, '')), 'B')
        ) STORED
    """)

    op.execute("CREATE INDEX IF NOT EXISTS articles_search_vector_idx ON articles USING GIN (search_vector)")
    # HNSW index on embedding is deferred until after backfill; HNSW builds
    # better with data already present.

    # Persistent cache so the same search query never embeds twice.
    # Bounds Gemini cost: each unique query embeds exactly once forever.
    op.execute("""
        CREATE TABLE IF NOT EXISTS search_query_cache (
            query_hash CHAR(40) PRIMARY KEY,
            query_text TEXT NOT NULL,
            embedding vector(768) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS search_query_cache_created_idx ON search_query_cache (created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS search_query_cache")
    op.execute("DROP INDEX IF EXISTS articles_search_vector_idx")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS search_vector")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS embedding")
