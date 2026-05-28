import logging
from datetime import date, datetime, timezone

from sqlalchemy import func, select

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.embedder import EmbeddingUsage
from buzz_news.models import EmbeddingUsageEvent

settings = get_settings()
log = logging.getLogger("buzz_news.embedding_budget")


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


async def daily_embedding_tokens(provider: str, model: str, usage_date: date | None = None) -> int:
    usage_date = usage_date or _today_utc()
    async with async_session_factory() as session:
        result = await session.execute(
            select(func.coalesce(func.sum(EmbeddingUsageEvent.input_tokens), 0))
            .where(EmbeddingUsageEvent.usage_date == usage_date)
            .where(EmbeddingUsageEvent.provider == provider)
            .where(EmbeddingUsageEvent.model == model)
        )
        return int(result.scalar() or 0)


async def remaining_embedding_tokens(provider: str, model: str) -> int:
    cap = int(settings.MAX_DAILY_EMBED_TOKENS or 0)
    if cap <= 0:
        return 2**31 - 1
    spent = await daily_embedding_tokens(provider, model)
    return max(0, cap - spent)


async def can_spend_embedding_tokens(provider: str, model: str, estimated_tokens: int) -> bool:
    cap = int(settings.MAX_DAILY_EMBED_TOKENS or 0)
    if cap <= 0:
        return True
    spent = await daily_embedding_tokens(provider, model)
    allowed = spent + max(0, estimated_tokens) <= cap
    if not allowed:
        log.warning(
            "Embedding budget exhausted provider=%s model=%s spent=%d estimated=%d cap=%d",
            provider,
            model,
            spent,
            estimated_tokens,
            cap,
        )
    return allowed


async def record_embedding_usage(usage: EmbeddingUsage, task_type: str) -> None:
    if usage.input_tokens <= 0 and usage.requests <= 0 and usage.item_count <= 0:
        return
    async with async_session_factory() as session:
        session.add(EmbeddingUsageEvent(
            usage_date=_today_utc(),
            provider=usage.provider,
            model=usage.model,
            task_type=task_type,
            input_tokens=usage.input_tokens,
            requests=usage.requests,
            item_count=usage.item_count,
        ))
        await session.commit()
