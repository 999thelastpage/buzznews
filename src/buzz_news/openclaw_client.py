import httpx
from buzz_news.config import get_settings

settings = get_settings()


async def call_skill(skill_path: str, payload: dict) -> dict:
    url = f"{settings.OPENCLAW_GATEWAY_URL}/{skill_path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


async def check_openclaw_health() -> bool:
    url = f"{settings.OPENCLAW_GATEWAY_URL}/health"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(url)
            return response.status_code == 200
        except Exception:
            return False
