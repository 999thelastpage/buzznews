"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION, JSONB


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger(), primary_key=True, server_default=sa.text("nextval('sources_id_seq')")),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("authority", sa.Numeric(3, 2), nullable=False, server_default="0.5"),
        sa.Column("is_tabloid", sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("extra", JSONB(), nullable=False, server_default="{}"),
    )

    op.create_table(
        "clusters",
        sa.Column("id", sa.BigInteger(), primary_key=True, server_default=sa.text("nextval('clusters_id_seq')")),
        sa.Column("centroid", ARRAY(DOUBLE_PRECISION), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("distinct_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("authority_sum", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("tabloid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("primary_language", sa.Text(), nullable=True),
        sa.Column("current_score", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default="FALSE"),
    )

    op.create_table(
        "raw_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, server_default=sa.text("nextval('raw_items_id_seq')")),
        sa.Column("source_id", sa.BigInteger(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("embedding", ARRAY(DOUBLE_PRECISION), nullable=True),
        sa.Column("minhash", sa.LargeBinary(), nullable=True),
        sa.Column("cluster_id", sa.BigInteger(), sa.ForeignKey("clusters.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("source_id", "external_id"),
    )
    op.create_index("raw_items_published_idx", "raw_items", ["published_at"], postgresql_using="btree")
    op.create_index("raw_items_cluster_idx", "raw_items", ["cluster_id"])

    op.create_table(
        "cluster_scores",
        sa.Column("id", sa.BigInteger(), primary_key=True, server_default=sa.text("nextval('cluster_scores_id_seq')")),
        sa.Column("cluster_id", sa.BigInteger(), sa.ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("source_diversity", sa.Numeric(), nullable=False),
        sa.Column("velocity", sa.Numeric(), nullable=False),
        sa.Column("authority", sa.Numeric(), nullable=False),
        sa.Column("time_decay", sa.Numeric(), nullable=False),
        sa.Column("anti_viral_penalty", sa.Numeric(), nullable=False),
        sa.Column("composite", sa.Numeric(), nullable=False),
    )
    op.create_index("cluster_scores_cluster_time_idx", "cluster_scores", ["cluster_id", "computed_at"])

    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger(), primary_key=True, server_default=sa.text("nextval('articles_id_seq')")),
        sa.Column("cluster_id", sa.BigInteger(), sa.ForeignKey("clusters.id"), nullable=False, unique=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("title_en", sa.Text(), nullable=False),
        sa.Column("title_hi", sa.Text(), nullable=True),
        sa.Column("summary_en", sa.Text(), nullable=False),
        sa.Column("summary_hi", sa.Text(), nullable=True),
        sa.Column("hero_image_url", sa.Text(), nullable=True),
        sa.Column("hero_image_credit", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("verifier_passed", sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("verifier_notes", JSONB(), nullable=True),
    )
    op.create_index("articles_published_idx", "articles", ["published_at"])
    op.create_index("articles_category_idx", "articles", ["category", "published_at"])
    op.create_index("articles_region_idx", "articles", ["region", "published_at"])

    op.create_table(
        "article_sources",
        sa.Column("id", sa.BigInteger(), primary_key=True, server_default=sa.text("nextval('article_sources_id_seq')")),
        sa.Column("article_id", sa.BigInteger(), sa.ForeignKey("articles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_item_id", sa.BigInteger(), sa.ForeignKey("raw_items.id"), nullable=False),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
    )
    op.create_index("article_sources_article_idx", "article_sources", ["article_id"])

    op.create_table(
        "rollups",
        sa.Column("id", sa.BigInteger(), primary_key=True, server_default=sa.text("nextval('rollups_id_seq')")),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("article_ids", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("period", "start_at", "end_at", "category", "region"),
    )

    op.create_table(
        "buzz_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, server_default=sa.text("nextval('buzz_events_id_seq')")),
        sa.Column("cluster_id", sa.BigInteger(), sa.ForeignKey("clusters.id"), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("velocity", sa.Numeric(), nullable=False),
        sa.Column("distinct_authoritative", sa.Integer(), nullable=False),
        sa.Column("composite", sa.Numeric(), nullable=False),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default="FALSE"),
    )
    op.create_index("buzz_events_fired_idx", "buzz_events", ["fired_at"])


def downgrade() -> None:
    op.drop_table("buzz_events")
    op.drop_table("rollups")
    op.drop_table("article_sources")
    op.drop_table("articles")
    op.drop_table("cluster_scores")
    op.drop_table("raw_items")
    op.drop_table("clusters")
    op.drop_table("sources")
