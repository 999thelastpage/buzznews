"""Hybrid (Postgres FTS + pgvector) search over published articles.

Cost-bounded by design: every unique query embeds exactly once forever
via search_query_cache, and a daily budget guard caps fresh embeddings
at MAX_DAILY_EMBEDS (≈ $0.075/day at Gemini's $0.00015/embed).
"""
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text

from buzz_news.db import async_session_factory
from buzz_news.embedder import embed_text
from buzz_news.models import SearchQueryCache

log = logging.getLogger("buzz_news.search")

# Hard daily cap. 500 * $0.00015 ≈ $0.075/day. Over budget → FTS-only.
MAX_DAILY_EMBEDS = 500

# Hybrid weights. Semantic favored slightly because user queries are
# usually paraphrases ("modi visit" → "PM India trip"), not exact terms.
W_FTS = 0.4
W_VEC = 0.6

# Garbage-title filter shared with home / today / month — same intent.
_GARBAGE_CLAUSE = (
    "a.title_en NOT ILIKE '%Unavailable%' "
    "AND a.title_en NOT ILIKE '%Access Restrictions%' "
    "AND a.title_en NOT ILIKE '%Inaccessible%'"
)


def _format_vector(vec) -> str:
    """pgvector accepts the text form '[v1,v2,...]' as input to a bind
    parameter when paired with CAST(... AS vector)."""
    return "[" + ",".join(f"{float(x):.7f}" for x in vec) + "]"


def _query_hash(query: str) -> str:
    return hashlib.sha1(query.strip().lower().encode("utf-8")).hexdigest()


async def _get_or_create_query_embedding(query: str) -> list[float] | None:
    """Return cached embedding for query if present; otherwise embed via
    Gemini and persist, unless the daily budget is exhausted (returns None).
    """
    h = _query_hash(query)

    async with async_session_factory() as session:
        cached = await session.execute(
            select(SearchQueryCache.embedding).where(SearchQueryCache.query_hash == h)
        )
        row = cached.scalar_one_or_none()
        if row is not None:
            return list(row)

    async with async_session_factory() as session:
        budget = await session.execute(
            text(
                "SELECT COUNT(*) FROM search_query_cache "
                "WHERE created_at >= date_trunc('day', now())"
            )
        )
        spent_today = budget.scalar() or 0

    if spent_today >= MAX_DAILY_EMBEDS:
        log.warning(
            f"Daily embed budget hit ({spent_today}/{MAX_DAILY_EMBEDS}); "
            f"falling back to FTS-only for query: {query!r}"
        )
        return None

    try:
        vec = embed_text(query, task_type="RETRIEVAL_QUERY").tolist()
    except Exception as e:
        log.warning(f"embed_text failed for query {query!r}: {e}")
        return None

    async with async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO search_query_cache (query_hash, query_text, embedding, created_at) "
                "VALUES (:h, :t, CAST(:v AS vector), :now) "
                "ON CONFLICT (query_hash) DO NOTHING"
            ),
            {"h": h, "t": query, "v": _format_vector(vec), "now": datetime.now(timezone.utc)},
        )
        await session.commit()

    return vec


async def hybrid_search(query: str, lang: str = "en", limit: int = 30) -> list[dict]:
    """Return up to `limit` articles matching `query`, ranked by a hybrid
    score combining Postgres FTS rank and pgvector cosine similarity.

    Falls back to FTS-only when the daily embedding budget is exhausted or
    the Gemini call fails. Empty query returns []."""
    query = (query or "").strip()
    if not query:
        return []

    qvec = await _get_or_create_query_embedding(query)
    hybrid = qvec is not None

    if hybrid:
        sql = text(f"""
            WITH q_fts AS (
                SELECT id, ts_rank_cd(search_vector, plainto_tsquery('simple', :q)) AS rank
                FROM articles
                WHERE search_vector @@ plainto_tsquery('simple', :q)
                ORDER BY rank DESC
                LIMIT 50
            ),
            q_vec AS (
                SELECT id, 1.0 - (embedding <=> CAST(:qvec AS vector)) AS sim
                FROM articles
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:qvec AS vector)
                LIMIT 50
            )
            SELECT a.id, a.slug, a.title_en, a.title_hi,
                   a.summary_en, a.summary_hi,
                   c.category AS category, a.region,
                   a.hero_image_url, a.published_at,
                   COALESCE(q_fts.rank, 0) * {W_FTS} + COALESCE(q_vec.sim, 0) * {W_VEC} AS combined,
                   c.source_count
            FROM articles a
            JOIN clusters c ON a.cluster_id = c.id
            LEFT JOIN q_fts ON a.id = q_fts.id
            LEFT JOIN q_vec ON a.id = q_vec.id
            WHERE (q_fts.id IS NOT NULL OR q_vec.id IS NOT NULL)
              AND {_GARBAGE_CLAUSE}
            ORDER BY combined DESC
            LIMIT :limit
        """)
        params = {"q": query, "qvec": _format_vector(qvec), "limit": limit}
    else:
        sql = text(f"""
            SELECT a.id, a.slug, a.title_en, a.title_hi,
                   a.summary_en, a.summary_hi,
                   c.category AS category, a.region,
                   a.hero_image_url, a.published_at,
                   ts_rank_cd(a.search_vector, plainto_tsquery('simple', :q)) AS combined,
                   c.source_count
            FROM articles a
            JOIN clusters c ON a.cluster_id = c.id
            WHERE a.search_vector @@ plainto_tsquery('simple', :q)
              AND {_GARBAGE_CLAUSE}
            ORDER BY combined DESC
            LIMIT :limit
        """)
        params = {"q": query, "limit": limit}

    async with async_session_factory() as session:
        result = await session.execute(sql, params)
        rows = result.fetchall()

    return [
        {
            "id": r.id,
            "slug": r.slug,
            "title_en": r.title_en,
            "title_hi": r.title_hi,
            "summary_en": r.summary_en,
            "summary_hi": r.summary_hi,
            "category": r.category or "general",
            "region": r.region,
            "hero_image_url": r.hero_image_url,
            "published_at": r.published_at,
            "source_count": r.source_count or 1,
            "score": float(r.combined or 0),
        }
        for r in rows
        if not (lang == "hi" and not r.title_hi)
    ]
