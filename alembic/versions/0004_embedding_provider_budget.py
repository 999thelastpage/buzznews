"""embedding provider metadata and usage budget tables

Revision ID: 0004_embedding_provider_budget
Revises: 0003_article_search
Create Date: 2026-05-28

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0004_embedding_provider_budget"
down_revision: Union[str, Sequence[str], None] = "0003_article_search"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS embedding_provider TEXT")
    op.execute("ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS embedding_model TEXT")
    op.execute("ALTER TABLE raw_items ADD COLUMN IF NOT EXISTS embedding_dim INTEGER")
    op.execute("""
        UPDATE raw_items
        SET embedding_provider = 'gemini',
            embedding_model = 'gemini-embedding-001',
            embedding_dim = 768
        WHERE embedding IS NOT NULL AND embedding_provider IS NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS raw_items_embedding_identity_idx ON raw_items (embedding_provider, embedding_model, embedding_dim)")

    op.execute("ALTER TABLE clusters ADD COLUMN IF NOT EXISTS centroid_provider TEXT")
    op.execute("ALTER TABLE clusters ADD COLUMN IF NOT EXISTS centroid_model TEXT")
    op.execute("ALTER TABLE clusters ADD COLUMN IF NOT EXISTS centroid_dim INTEGER")
    op.execute("""
        UPDATE clusters
        SET centroid_provider = 'gemini',
            centroid_model = 'gemini-embedding-001',
            centroid_dim = 768
        WHERE centroid IS NOT NULL AND centroid_provider IS NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS clusters_centroid_identity_idx ON clusters (centroid_provider, centroid_model, centroid_dim)")

    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS embedding_provider TEXT")
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS embedding_model TEXT")
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS embedding_dim INTEGER")
    op.execute("""
        UPDATE articles
        SET embedding_provider = 'gemini',
            embedding_model = 'gemini-embedding-001',
            embedding_dim = 768
        WHERE embedding IS NOT NULL AND embedding_provider IS NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS articles_embedding_identity_idx ON articles (embedding_provider, embedding_model, embedding_dim)")

    op.execute("ALTER TABLE search_query_cache ADD COLUMN IF NOT EXISTS embedding_provider TEXT")
    op.execute("ALTER TABLE search_query_cache ADD COLUMN IF NOT EXISTS embedding_model TEXT")
    op.execute("ALTER TABLE search_query_cache ADD COLUMN IF NOT EXISTS embedding_dim INTEGER")
    op.execute("""
        UPDATE search_query_cache
        SET embedding_provider = 'gemini',
            embedding_model = 'gemini-embedding-001',
            embedding_dim = 768
        WHERE embedding_provider IS NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS search_query_cache_identity_idx ON search_query_cache (embedding_provider, embedding_model, embedding_dim)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS embedding_usage_events (
            id BIGSERIAL PRIMARY KEY,
            usage_date DATE NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            task_type TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            requests INTEGER NOT NULL DEFAULT 0,
            item_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS embedding_usage_events_day_idx ON embedding_usage_events (usage_date, provider, model)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS llm_usage_events (
            id BIGSERIAL PRIMARY KEY,
            usage_date DATE NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            cluster_id BIGINT,
            lang TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS llm_usage_events_day_idx ON llm_usage_events (usage_date, provider, model)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS llm_usage_events")
    op.execute("DROP TABLE IF EXISTS embedding_usage_events")
    op.execute("DROP INDEX IF EXISTS search_query_cache_identity_idx")
    op.execute("ALTER TABLE search_query_cache DROP COLUMN IF EXISTS embedding_dim")
    op.execute("ALTER TABLE search_query_cache DROP COLUMN IF EXISTS embedding_model")
    op.execute("ALTER TABLE search_query_cache DROP COLUMN IF EXISTS embedding_provider")
    op.execute("DROP INDEX IF EXISTS articles_embedding_identity_idx")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS embedding_dim")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS embedding_model")
    op.execute("ALTER TABLE articles DROP COLUMN IF EXISTS embedding_provider")
    op.execute("DROP INDEX IF EXISTS clusters_centroid_identity_idx")
    op.execute("ALTER TABLE clusters DROP COLUMN IF EXISTS centroid_dim")
    op.execute("ALTER TABLE clusters DROP COLUMN IF EXISTS centroid_model")
    op.execute("ALTER TABLE clusters DROP COLUMN IF EXISTS centroid_provider")
    op.execute("DROP INDEX IF EXISTS raw_items_embedding_identity_idx")
    op.execute("ALTER TABLE raw_items DROP COLUMN IF EXISTS embedding_dim")
    op.execute("ALTER TABLE raw_items DROP COLUMN IF EXISTS embedding_model")
    op.execute("ALTER TABLE raw_items DROP COLUMN IF EXISTS embedding_provider")
