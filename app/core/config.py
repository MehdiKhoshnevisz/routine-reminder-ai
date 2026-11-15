from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Routine AI"
    DATABASE_URL: str  # required, set in .env (see .env.example)
    # OpenRouter (OpenAI-compatible API). Key: https://openrouter.ai/keys
    OPENROUTER_API_KEY: str = ""  # set in .env, never commit a real key
    # Any model id from https://openrouter.ai/models
    OPENROUTER_MODEL: str = "openai/gpt-4o-mini"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # Per-request timeout (seconds) and automatic retries on 429/5xx/connection errors.
    OPENROUTER_TIMEOUT: float = 30.0
    OPENROUTER_MAX_RETRIES: int = 2
    TELEGRAM_BOT_TOKEN: str = ""
    DEFAULT_TIMEZONE: str = "UTC"
    REMINDER_CHECK_SECONDS: int = 30
    # A reminder is still sent if the app was down/busy for up to this many minutes after the routine time.
    REMINDER_GRACE_MINUTES: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
