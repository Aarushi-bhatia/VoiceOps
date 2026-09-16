"""Application settings, loaded from the environment (12-factor)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["mock", "deepgram", "anthropic", "elevenlabs", "twilio"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Core ----
    voiceops_env: str = "local"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
    )

    # ---- Persistence ----
    # When DATABASE_URL is unset we fall back to a local SQLite file so the
    # platform is runnable without Postgres. Compose sets the real URL.
    database_url: str = "sqlite+aiosqlite:///./voiceops.db"
    db_echo: bool = False

    # When REDIS_URL is unset we fall back to an in-process Redis stub. That
    # stub is single-process only: API and worker must then share a process
    # (see `voiceops-api --with-worker`) or run against a real Redis.
    redis_url: str | None = None

    # ---- Worker ----
    worker_concurrency: int = 4
    worker_poll_interval_ms: int = 250
    queue_lease_seconds: int = 120
    queue_namespace: str = "voiceops"

    # ---- Retry policy ----
    retry_max_attempts: int = 4
    retry_base_delay_seconds: float = 15.0
    retry_max_delay_seconds: float = 3600.0
    retry_backoff_multiplier: float = 2.0
    retry_jitter_ratio: float = 0.2

    # ---- Providers ----
    stt_provider: str = "mock"
    llm_provider: str = "mock"
    tts_provider: str = "mock"
    telephony_provider: str = "mock"

    deepgram_api_key: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    elevenlabs_api_key: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    # Public origin Twilio can reach for Media Streams (a tunnel in dev).
    twilio_public_base_url: str | None = None

    # Deterministic seed for the mock voice stack, so simulations and tests
    # replay identically.
    mock_seed: int = 1337
    # Speeds up the mock pipeline in tests; 1.0 = simulate realistic latency.
    mock_latency_scale: float = 1.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def uses_real_redis(self) -> bool:
        return bool(self.redis_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
