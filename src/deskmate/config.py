from functools import lru_cache
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://deskmate:deskmate@localhost:5432/deskmate"
    redis_url: str = "redis://localhost:6379/0"
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = 1024
    prompt_version: str = "v1"
    api_key: str
    bot_mode: Literal["off", "polling", "webhook"] = "off"
    telegram_bot_token: str | None = None
    webhook_base_url: str | None = None
    webhook_secret: str | None = None
    context_window: int = 20
    context_ttl_s: int = 3600
    cache_ttl_s: int = 3600
    dedupe_ttl_s: int = 3600
    dialog_idle_minutes: int = 30

    @model_validator(mode="after")
    def _check_bot_settings(self) -> Self:
        required: list[str] = []
        if self.bot_mode != "off":
            required.append("telegram_bot_token")
        if self.bot_mode == "webhook":
            required += ["webhook_base_url", "webhook_secret"]
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            raise ValueError(f"BOT_MODE={self.bot_mode} requires: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
