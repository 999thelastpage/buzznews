from datetime import datetime
from typing import Any
from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    Date,
    DateTime,
    FetchedValue,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, DOUBLE_PRECISION, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector
import enum


class Base(DeclarativeBase):
    pass


class SourceKind(str, enum.Enum):
    RSS = "rss"
    REDDIT = "reddit"
    HN = "hn"
    GDELT = "gdelt"
    TAVILY = "tavily"


class SourceLanguage(str, enum.Enum):
    EN = "en"
    HI = "hi"


class Period(str, enum.Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    authority: Mapped[float] = mapped_column(Numeric(3, 2), default=0.5)
    is_tabloid: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    centroid: Mapped[list[float] | None] = mapped_column(ARRAY(DOUBLE_PRECISION), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    distinct_sources: Mapped[int] = mapped_column(Integer, default=0)
    authority_sum: Mapped[float] = mapped_column(Numeric, default=0)
    tabloid_count: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_language: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_score: Mapped[float] = mapped_column(Numeric, default=0)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    centroid_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    centroid_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    centroid_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RawItem(Base):
    __tablename__ = "raw_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(DOUBLE_PRECISION), nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minhash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    cluster_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("clusters.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        Index("raw_items_published_idx", published_at.desc()),
        Index("raw_items_cluster_idx", cluster_id),
    )


class ClusterScore(Base):
    __tablename__ = "cluster_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    source_diversity: Mapped[float] = mapped_column(Numeric, nullable=False)
    velocity: Mapped[float] = mapped_column(Numeric, nullable=False)
    authority: Mapped[float] = mapped_column(Numeric, nullable=False)
    time_decay: Mapped[float] = mapped_column(Numeric, nullable=False)
    anti_viral_penalty: Mapped[float] = mapped_column(Numeric, nullable=False)
    composite: Mapped[float] = mapped_column(Numeric, nullable=False)


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("clusters.id"), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title_en: Mapped[str] = mapped_column(Text, nullable=False)
    title_hi: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_en: Mapped[str] = mapped_column(Text, nullable=False)
    summary_hi: Mapped[str | None] = mapped_column(Text, nullable=True)
    hero_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    hero_image_credit: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    verifier_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    verifier_notes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # search_vector is a STORED GENERATED column maintained by Postgres; we
    # never write to it. server_default + FetchedValue tells SQLAlchemy to
    # leave it alone on INSERT/UPDATE.
    search_vector: Mapped[Any | None] = mapped_column(
        TSVECTOR, nullable=True, server_default=FetchedValue(),
    )


class ArticleSource(Base):
    __tablename__ = "article_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    article_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)
    raw_item_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("raw_items.id"), nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class Rollup(Base):
    __tablename__ = "rollups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period: Mapped[str] = mapped_column(Text, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(Text, nullable=True)
    article_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SearchQueryCache(Base):
    __tablename__ = "search_query_cache"

    query_hash: Mapped[str] = mapped_column(CHAR(40), primary_key=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    embedding_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class EmbeddingUsageEvent(Base):
    __tablename__ = "embedding_usage_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usage_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class LLMUsageEvent(Base):
    __tablename__ = "llm_usage_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usage_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    cluster_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lang: Mapped[str | None] = mapped_column(Text, nullable=True)
    task: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BuzzEvent(Base):
    __tablename__ = "buzz_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("clusters.id"), nullable=False)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    velocity: Mapped[float] = mapped_column(Numeric, nullable=False)
    distinct_authoritative: Mapped[int] = mapped_column(Integer, nullable=False)
    composite: Mapped[float] = mapped_column(Numeric, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
