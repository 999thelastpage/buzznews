from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://buzz:CHANGE_ME@localhost:5432/buzz_news"
    DATABASE_URL_RO: str = "postgresql://buzz_ro:CHANGE_ME_RO@localhost:5432/buzz_news"
    REDIS_URL: str = "redis://localhost:6379/0"

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL_TEXT: str = "gemini-2.0-flash"
    GEMINI_MODEL_EMBED: str = "gemini-embedding-001"
    EMBED_DIM: int = 768
    EMBED_PROVIDER: str = "openai"

    OPENAI_API_KEY: str = ""
    OPENAI_EMBED_MODEL: str = "text-embedding-3-small"
    OPENAI_EMBED_DIM: int = 768
    MAX_DAILY_EMBED_TOKENS: int = 1_500_000
    GEMINI_FALLBACK_DAILY_CAP: int = 5

    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"

    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"

    UNSPLASH_ACCESS_KEY: str = ""
    PEXELS_API_KEY: str = ""

    REDDIT_USER_AGENT: str = "buzz-news/0.1 (by /u/TODO_PRE_LAUNCH contact:placeholder@example.com)"

    TAVILY_API_KEY: str = "TODO_BEFORE_PHASE_1"

    OPENCLAW_GATEWAY_URL: str = "http://127.0.0.1:19262"
    OPENCLAW_BROWSER_FALLBACK_ENABLED: bool = False

    BUZZ_WEBHOOK_URL: str = ""

    CLOUDFLARE_ZONE_ID: str = "TODO_PRE_LAUNCH"
    CLOUDFLARE_API_TOKEN: str = "TODO_PRE_LAUNCH"
    CLOUDFLARE_PURGE_ENABLED: bool = False

    SITE_BASE_URL: str = "https://TODO_PRE_LAUNCH"
    SITE_HOST: str = "TODO_PRE_LAUNCH"
    STATIC_DIR: str = "/var/lib/buzz-news/static"
    LOG_DIR: str = "/var/log/buzz-news"
    TZ: str = "Asia/Kolkata"

    SCORE_TIME_GRAVITY: float = 1.5
    SCORE_DIVERSITY_CAP: int = 8
    BUZZ_VELOCITY_THRESHOLD: float = 0.4
    BUZZ_MIN_AUTHORITATIVE: int = 3

    FETCH_INTERVAL_MIN: int = 15
    SCORE_INTERVAL_MIN: int = 5
    PUBLISH_INTERVAL_MIN: int = 30
    TOP_N_PER_CYCLE: int = 10

    RETENTION_RAW_ITEMS_DAYS: int = 90
    RETENTION_CLUSTER_SCORES_DAYS: int = 30
    RETENTION_BUZZ_EVENTS_DAYS: int = 365
    RETENTION_IMAGES_DAYS: int = 365
    RETENTION_SEARCH_CACHE_DAYS: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()
