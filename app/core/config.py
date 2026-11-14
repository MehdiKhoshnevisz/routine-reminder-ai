from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Routine AI"
    DATABASE_URL: str = "postgresql+psycopg://routine:routine@localhost:5432/routine_ai"
    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"
    TELEGRAM_BOT_TOKEN: str = ""
    DEFAULT_TIMEZONE: str = "UTC"
    REMINDER_CHECK_SECONDS: int = 30
    # A reminder is still sent if the app was down/busy for up to this many minutes after the routine time.
    REMINDER_GRACE_MINUTES: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
