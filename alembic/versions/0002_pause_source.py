"""add pause_source function

Revision ID: 0002_pause_source
Revises: 0001_initial
Create Date: 2026-05-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_pause_source"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION pause_source(p_slug TEXT)
        RETURNS BOOLEAN
        LANGUAGE plpgsql
        SECURITY DEFINER
        AS $$
        BEGIN
            UPDATE sources SET enabled = FALSE WHERE slug = p_slug;
            RETURN FOUND;
        END;
        $$;
    """)
    op.execute("GRANT EXECUTE ON FUNCTION pause_source(TEXT) TO buzz_ro;")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION pause_source(TEXT) FROM buzz_ro;")
    op.execute("DROP FUNCTION IF EXISTS pause_source(TEXT);")
