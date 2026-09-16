"""Retry decisions: exponential backoff with jitter, and dead-lettering."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.core.config import Settings
from app.core.enums import FailureCategory


class RetryAction(StrEnum):
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class RetryDecision:
    action: RetryAction
    delay_seconds: float
    retry_at: datetime | None
    attempt: int
    reason: str

    @property
    def should_retry(self) -> bool:
        return self.action is RetryAction.RETRY


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff: ``base * multiplier ** (attempt - 1)``, capped, jittered.

    Jitter is symmetric around the computed delay (``jitter_ratio`` of it) so a
    burst of simultaneous failures does not produce a synchronised retry spike.
    """

    max_attempts: int = 4
    base_delay_seconds: float = 15.0
    max_delay_seconds: float = 3600.0
    multiplier: float = 2.0
    jitter_ratio: float = 0.2

    @classmethod
    def from_settings(cls, settings: Settings) -> RetryPolicy:
        return cls(
            max_attempts=settings.retry_max_attempts,
            base_delay_seconds=settings.retry_base_delay_seconds,
            max_delay_seconds=settings.retry_max_delay_seconds,
            multiplier=settings.retry_backoff_multiplier,
            jitter_ratio=settings.retry_jitter_ratio,
        )

    def backoff_seconds(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """Delay before attempt ``attempt + 1``, given ``attempt`` already failed."""
        exponent = max(attempt - 1, 0)
        raw = self.base_delay_seconds * (self.multiplier**exponent)
        capped = min(raw, self.max_delay_seconds)
        if self.jitter_ratio <= 0:
            return capped
        jitter_source = rng or random
        spread = capped * self.jitter_ratio
        jittered = capped + jitter_source.uniform(-spread, spread)
        return max(0.0, min(jittered, self.max_delay_seconds))

    def decide(
        self,
        *,
        attempt: int,
        category: FailureCategory,
        max_attempts: int | None = None,
        now: datetime | None = None,
        rng: random.Random | None = None,
    ) -> RetryDecision:
        """Decide what to do after ``attempt`` failed with ``category``."""
        now = now or datetime.now(UTC)
        limit = max_attempts if max_attempts is not None else self.max_attempts

        if not category.is_transient:
            return RetryDecision(
                action=RetryAction.DEAD_LETTER,
                delay_seconds=0.0,
                retry_at=None,
                attempt=attempt,
                reason=f"{category} is a permanent failure",
            )

        if attempt >= limit:
            return RetryDecision(
                action=RetryAction.DEAD_LETTER,
                delay_seconds=0.0,
                retry_at=None,
                attempt=attempt,
                reason=f"exhausted {limit} attempts",
            )

        delay = self.backoff_seconds(attempt, rng=rng)
        return RetryDecision(
            action=RetryAction.RETRY,
            delay_seconds=delay,
            retry_at=now + timedelta(seconds=delay),
            attempt=attempt,
            reason=f"transient {category}, attempt {attempt + 1}/{limit}",
        )
