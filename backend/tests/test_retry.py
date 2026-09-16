"""Retry policy: backoff growth, capping, jitter and permanence."""

from __future__ import annotations

import random
from datetime import UTC, datetime

from app.core.enums import FailureCategory
from app.queue.retry import RetryAction, RetryPolicy


def test_backoff_grows_exponentially_without_jitter():
    policy = RetryPolicy(base_delay_seconds=10, multiplier=2.0, jitter_ratio=0.0)
    assert [policy.backoff_seconds(a) for a in (1, 2, 3, 4)] == [10, 20, 40, 80]


def test_backoff_is_capped():
    policy = RetryPolicy(
        base_delay_seconds=10, multiplier=10.0, max_delay_seconds=500, jitter_ratio=0
    )
    assert policy.backoff_seconds(9) == 500


def test_jitter_stays_within_ratio_and_is_never_negative():
    policy = RetryPolicy(base_delay_seconds=100, multiplier=1.0, jitter_ratio=0.25)
    rng = random.Random(7)
    samples = [policy.backoff_seconds(1, rng=rng) for _ in range(500)]
    assert all(75 <= s <= 125 for s in samples)
    assert len(set(samples)) > 1, "jitter should spread retries out"


def test_transient_failures_retry_until_attempts_run_out():
    policy = RetryPolicy(max_attempts=3, jitter_ratio=0)
    now = datetime.now(UTC)

    first = policy.decide(attempt=1, category=FailureCategory.NETWORK, now=now)
    assert first.action is RetryAction.RETRY
    assert first.retry_at is not None and first.retry_at > now

    last = policy.decide(attempt=3, category=FailureCategory.NETWORK, now=now)
    assert last.action is RetryAction.DEAD_LETTER
    assert "exhausted" in last.reason


def test_permanent_failures_never_retry():
    policy = RetryPolicy(max_attempts=10)
    for category in (
        FailureCategory.INVALID_NUMBER,
        FailureCategory.DO_NOT_CALL,
        FailureCategory.AGENT_CONFIG,
    ):
        decision = policy.decide(attempt=1, category=category)
        assert decision.action is RetryAction.DEAD_LETTER, category
        assert not decision.should_retry


def test_per_call_max_attempts_overrides_the_policy():
    policy = RetryPolicy(max_attempts=10)
    decision = policy.decide(attempt=2, category=FailureCategory.BUSY, max_attempts=2)
    assert decision.action is RetryAction.DEAD_LETTER
