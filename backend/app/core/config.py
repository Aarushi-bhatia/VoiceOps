"""Application settings, loaded from the environment (12-factor)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    # "<key>:<role>,<key>:<role>" with roles viewer/operator/admin.
    # Empty disables authentication entirely; /health reports which.
    api_keys: str | None = None
    # NoDecode: pydantic-settings JSON-decodes complex types from the
    # environment before validators run, so a plain comma-separated
    # CORS_ORIGINS would fail to parse. This hands the raw string to the
    # validator below instead.
    cors_origins: Annotated[list[str], NoDecode] = Field(
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

    # ---- Admission control / backpressure ----
    # Reject new calls once this many are waiting. 0 disables the cap.
    max_queue_depth: int = 50_000
    # Calls older than this are dropped rather than placed: a support call
    # answered six hours late is worse than no call. 0 disables.
    max_call_age_seconds: int = 21_600
    # Carriers allow roughly one call per second per number, which is the real
    # ceiling on throughput. Off by default and set per deployment: the right
    # figure depends on the carrier and the numbers in use, and a guessed
    # default would silently throttle development to a crawl. 0 disables.
    carrier_calls_per_second: float = 0.0
    carrier_burst: int = 5
    # Outbound calling window in the callee's local time, 24h clock. Also off by
    # default, and for a sharper reason: a global default window is wrong for
    # most callees, and one that is wrong silently defers every call. Set it per
    # deployment (compose and the Kubernetes config both do). Per-call overrides
    # come from a "timezone" key in the call's metadata. start == end disables.
    calling_hours_start: int = 0
    calling_hours_end: int = 0
    calling_hours_timezone: str = "UTC"

    # ---- Retry policy ----
    retry_max_attempts: int = 4
    retry_base_delay_seconds: float = 15.0
    retry_max_delay_seconds: float = 3600.0
    retry_backoff_multiplier: float = 2.0
    retry_jitter_ratio: float = 0.2

    # ---- Cost model ----
    # Estimates only. Replace with your contracted rates; they feed the cost
    # figures on the dashboard and nothing else.
    cost_stt_cents_per_minute: float = 0.43
    cost_tts_cents_per_1k_chars: float = 3.0
    cost_telephony_cents_per_minute: float = 1.3
    cost_llm_cents_per_1k_input: float = 0.5
    cost_llm_cents_per_1k_output: float = 2.5

    # ---- Retention ----
    # Transcripts hold personal data, so they are deleted on a schedule rather
    # than kept forever. 0 keeps everything.
    retention_days: int = 90

    # ---- Providers ----
    stt_provider: str = "mock"
    llm_provider: str = "mock"
    tts_provider: str = "mock"
    telephony_provider: str = "mock"

    deepgram_api_key: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    # Thinking tokens are drawn from max_tokens and add latency; 0 turns
    # them off, which is what a live call wants. -1 leaves it to Gemini.
    gemini_thinking_budget: int = 0
    # The adapter absorbs the provider's own rate limits and overloads
    # rather than letting them abandon a call mid-conversation.
    gemini_max_attempts: int = 4
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
        """Accept a comma-separated list, or a JSON array."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            import json

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        return [origin.strip() for origin in text.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def uses_real_redis(self) -> bool:
        return bool(self.redis_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
