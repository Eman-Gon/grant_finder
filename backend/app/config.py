from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ANTHROPIC_API_KEY: str = ""
    PREMIUM_MODEL: str = "claude-sonnet-4-6"

    GRANTS_GOV_API_KEY: str = ""
    SIMPLER_GRANTS_API_KEY: str = ""

    N_GRANTS: int = 20
    SEMAPHORE: int = 8
    SCRAPE_TIMEOUT_S: float = 10.0
    LLM_TIMEOUT_S: float = 45.0


settings = Settings()
