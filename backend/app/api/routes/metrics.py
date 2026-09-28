"""Prometheus metrics.

Written by hand rather than pulling in a client library: the exposition format
is a few lines of text, and the numbers already exist in Redis counters and the
database. Scrape this and you can alert on the things that actually go wrong -
a growing backlog, a filling dead-letter queue, or throttling that means the
carrier is the bottleneck.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import func, select

from app.api.deps import DbSession, Queue
from app.core.enums import CallStatus
from app.db.models import Call
from app.services import analytics

router = APIRouter(tags=["metrics"])

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _metric(name: str, kind: str, help_text: str, samples: list[tuple[str, float]]) -> str:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
    lines += [f"{name}{labels} {value}" for labels, value in samples]
    return "\n".join(lines)


@router.get("/metrics", include_in_schema=False)
async def metrics(session: DbSession, queue: Queue) -> Response:
    stats = await queue.stats()

    blocks = [
        _metric(
            "voiceops_queue_depth",
            "gauge",
            "Calls currently in each part of the queue.",
            [
                ('{state="ready"}', stats.ready),
                ('{state="scheduled"}', stats.scheduled),
                ('{state="processing"}', stats.processing),
                ('{state="dead_letter"}', stats.dead_letter),
            ],
        ),
        _metric(
            "voiceops_queue_events_total",
            "counter",
            "Lifetime queue events since the counters were last reset.",
            [(f'{{event="{name}"}}', value) for name, value in sorted(stats.counters.items())],
        ),
    ]

    status_rows = await session.execute(
        select(Call.status, func.count(Call.id)).group_by(Call.status)
    )
    blocks.append(
        _metric(
            "voiceops_calls",
            "gauge",
            "Calls in the database by status.",
            [(f'{{status="{status}"}}', int(count)) for status, count in status_rows],
        )
    )

    outcome_rows = await session.execute(
        select(Call.outcome, func.count(Call.id))
        .where(Call.outcome.is_not(None))
        .group_by(Call.outcome)
    )
    blocks.append(
        _metric(
            "voiceops_call_outcomes",
            "gauge",
            "Completed calls by business outcome.",
            [(f'{{outcome="{outcome}"}}', int(count)) for outcome, count in outcome_rows],
        )
    )

    failure_rows = await session.execute(
        select(Call.failure_category, func.count(Call.id))
        .where(Call.failure_category.is_not(None))
        .group_by(Call.failure_category)
    )
    blocks.append(
        _metric(
            "voiceops_call_failures",
            "gauge",
            "Calls by failure category.",
            [(f'{{category="{category}"}}', int(count)) for category, count in failure_rows],
        )
    )

    latency = await analytics.turn_latency(session)
    blocks.append(
        _metric(
            "voiceops_turn_latency_milliseconds",
            "gauge",
            "Per-turn response latency, speech recognised to reply started.",
            [
                ('{quantile="0.5"}', latency.p50_ms),
                ('{quantile="0.9"}', latency.p90_ms),
                ('{quantile="0.95"}', latency.p95_ms),
                ('{quantile="0.99"}', latency.p99_ms),
            ],
        )
    )
    blocks.append(
        _metric(
            "voiceops_turn_latency_samples",
            "gauge",
            "Turns the latency quantiles were computed from.",
            [("", latency.samples)],
        )
    )

    # The single most useful alerting signal: work that needs a human.
    blocks.append(
        _metric(
            "voiceops_dead_letter_calls",
            "gauge",
            "Calls that exhausted retries or failed permanently.",
            [("", stats.dead_letter)],
        )
    )
    in_flight = await session.scalar(
        select(func.count())
        .select_from(Call)
        .where(Call.status.in_([CallStatus.DIALING, CallStatus.IN_PROGRESS]))
    )
    blocks.append(
        _metric(
            "voiceops_calls_in_flight",
            "gauge",
            "Calls currently being dialled or spoken.",
            [("", int(in_flight or 0))],
        )
    )

    return Response("\n\n".join(blocks) + "\n", media_type=CONTENT_TYPE)
