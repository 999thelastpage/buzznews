from datetime import date, datetime, timezone

from sqlalchemy import func, select

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.models import LLMUsageEvent

settings = get_settings()

FREE_PROVIDERS = {"cerebras", "groq"}


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _provider_from_spec(provider_or_spec: str) -> str:
    return (provider_or_spec.split(":", 1)[0] if provider_or_spec else "").strip().lower()


async def gemini_fallback_available() -> bool:
    cap = int(settings.GEMINI_FALLBACK_DAILY_CAP or 0)
    if cap <= 0:
        return False
    async with async_session_factory() as session:
        result = await session.execute(
            select(func.count(LLMUsageEvent.id))
            .where(LLMUsageEvent.usage_date == _today_utc())
            .where(LLMUsageEvent.provider == "gemini")
            .where(LLMUsageEvent.model == settings.GEMINI_MODEL_TEXT)
        )
        return int(result.scalar() or 0) < cap


async def record_gemini_fallback(cluster_id: int, lang: str) -> None:
    await record_llm_usage(
        provider="gemini",
        model=settings.GEMINI_MODEL_TEXT,
        cluster_id=cluster_id,
        lang=lang,
        task="fallback",
        success=True,
    )


async def record_llm_usage(
    *,
    provider: str,
    model: str,
    cluster_id: int | None = None,
    article_id: int | None = None,
    lang: str | None = None,
    task: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    success: bool = True,
    error_type: str | None = None,
) -> None:
    async with async_session_factory() as session:
        session.add(LLMUsageEvent(
            usage_date=_today_utc(),
            provider=provider,
            model=model,
            cluster_id=cluster_id,
            article_id=article_id,
            lang=lang,
            task=task,
            input_tokens=max(0, int(input_tokens or 0)),
            output_tokens=max(0, int(output_tokens or 0)),
            success=success,
            error_type=error_type,
        ))
        await session.commit()


async def llm_tokens_used_today(provider: str | None = None, providers: set[str] | None = None) -> int:
    async with async_session_factory() as session:
        stmt = select(
            func.coalesce(
                func.sum(LLMUsageEvent.input_tokens + LLMUsageEvent.output_tokens),
                0,
            )
        ).where(LLMUsageEvent.usage_date == _today_utc())
        if provider:
            stmt = stmt.where(LLMUsageEvent.provider == provider)
        if providers:
            stmt = stmt.where(LLMUsageEvent.provider.in_(providers))
        result = await session.execute(stmt)
        return int(result.scalar() or 0)


async def free_provider_available(provider_or_spec: str, estimated_tokens: int = 0) -> bool:
    provider = _provider_from_spec(provider_or_spec)
    if provider not in FREE_PROVIDERS:
        return True

    projected_total = await llm_tokens_used_today(providers=FREE_PROVIDERS) + max(0, int(estimated_tokens or 0))
    if projected_total > int(settings.FREE_LLM_DAILY_TOKEN_SOFT_CAP or 0):
        return False

    if provider == "groq":
        projected_groq = await llm_tokens_used_today("groq") + max(0, int(estimated_tokens or 0))
        if projected_groq > int(settings.GROQ_DAILY_TOKEN_SOFT_CAP or 0):
            return False

    return True
