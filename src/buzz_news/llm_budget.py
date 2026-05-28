from datetime import date, datetime, timezone

from sqlalchemy import func, select

from buzz_news.config import get_settings
from buzz_news.db import async_session_factory
from buzz_news.models import LLMUsageEvent

settings = get_settings()


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


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
    async with async_session_factory() as session:
        session.add(LLMUsageEvent(
            usage_date=_today_utc(),
            provider="gemini",
            model=settings.GEMINI_MODEL_TEXT,
            cluster_id=cluster_id,
            lang=lang,
        ))
        await session.commit()
