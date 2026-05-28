"""extend llm usage events for routing budgets

Revision ID: 0005_llm_usage_details
Revises: 0004_embedding_provider_budget
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0005_llm_usage_details"
down_revision: Union[str, Sequence[str], None] = "0004_embedding_provider_budget"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS article_id BIGINT")
    op.execute("ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS task TEXT")
    op.execute("ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS input_tokens INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS output_tokens INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS success BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute("ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS error_type TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS llm_usage_events_task_day_idx ON llm_usage_events (usage_date, task, provider, model)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS llm_usage_events_task_day_idx")
    op.execute("ALTER TABLE llm_usage_events DROP COLUMN IF EXISTS error_type")
    op.execute("ALTER TABLE llm_usage_events DROP COLUMN IF EXISTS success")
    op.execute("ALTER TABLE llm_usage_events DROP COLUMN IF EXISTS output_tokens")
    op.execute("ALTER TABLE llm_usage_events DROP COLUMN IF EXISTS input_tokens")
    op.execute("ALTER TABLE llm_usage_events DROP COLUMN IF EXISTS task")
    op.execute("ALTER TABLE llm_usage_events DROP COLUMN IF EXISTS article_id")
