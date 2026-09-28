#!/usr/bin/env python
"""Delete call records past the retention window.

Transcripts contain what customers said, which is personal data: keeping them
indefinitely is a liability as well as a storage cost. This deletes finished
calls older than the window, and their turns and events with them.

    python scripts/retention.py --dry-run          # show what would go
    python scripts/retention.py                    # use RETENTION_DAYS
    python scripts/retention.py --days 30          # override

In-flight calls are never touched, whatever their age.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.enums import CallStatus  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.models import Call, CallEvent, CallTurn  # noqa: E402
from app.db.session import session_scope  # noqa: E402

TERMINAL = (CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.CANCELED)


async def purge(days: int, *, dry_run: bool, batch: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    removed = 0

    while True:
        async with session_scope() as session:
            ids = (
                await session.scalars(
                    select(Call.id)
                    .where(Call.status.in_(TERMINAL), Call.created_at < cutoff)
                    .limit(batch)
                )
            ).all()
            if not ids:
                break

            turns = await session.scalar(
                select(func.count()).select_from(CallTurn).where(CallTurn.call_id.in_(ids))
            )
            events = await session.scalar(
                select(func.count()).select_from(CallEvent).where(CallEvent.call_id.in_(ids))
            )

            if dry_run:
                print(f"  would delete {len(ids)} calls, {turns} turns, {events} events")
                return len(ids)

            # Explicit deletes: ON DELETE CASCADE is not enforced by SQLite
            # unless foreign keys are switched on for the connection.
            await session.execute(delete(CallTurn).where(CallTurn.call_id.in_(ids)))
            await session.execute(delete(CallEvent).where(CallEvent.call_id.in_(ids)))
            await session.execute(delete(Call).where(Call.id.in_(ids)))
            removed += len(ids)
            print(f"  deleted {len(ids)} calls ({turns} turns, {events} events)")

        if len(ids) < batch:
            break
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=None, help="override RETENTION_DAYS")
    parser.add_argument("--dry-run", action="store_true", help="report without deleting")
    parser.add_argument("--batch", type=int, default=500, help="rows per transaction")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    days = args.days if args.days is not None else settings.retention_days
    if days <= 0:
        print("retention is disabled (days <= 0); nothing to do")
        return

    print(f"retention: deleting finished calls older than {days} days")
    total = asyncio.run(purge(days, dry_run=args.dry_run, batch=args.batch))
    print(f"{'would remove' if args.dry_run else 'removed'} {total} calls")


if __name__ == "__main__":
    main()
