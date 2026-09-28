"""The do-not-call list.

Suppression is a legal obligation in most jurisdictions, so it is enforced in
two places: a call for a suppressed number is refused at creation, and checked
again before dialling in case the number was added while the call was queued.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import Queue
from app.core.security import RequireAdmin, RequireViewer
from app.queue.throttle import DoNotCallList, normalise_number
from app.schemas.common import Message

router = APIRouter(prefix="/suppression", tags=["suppression"])


class SuppressionEntry(BaseModel):
    number: str = Field(min_length=3, max_length=32)
    reason: str = Field(default="", max_length=200)


def _list(queue) -> DoNotCallList:
    return DoNotCallList(queue.redis, queue.settings.queue_namespace)


@router.get("", response_model=list[SuppressionEntry], dependencies=[RequireViewer])
async def list_suppressed(queue: Queue) -> list[SuppressionEntry]:
    entries = await _list(queue).all()
    return [SuppressionEntry(number=number, reason=reason) for number, reason in entries.items()]


@router.post(
    "",
    response_model=Message,
    status_code=status.HTTP_201_CREATED,
    dependencies=[RequireAdmin],
)
async def suppress(entry: SuppressionEntry, queue: Queue) -> Message:
    await _list(queue).add(entry.number, entry.reason)
    return Message(detail=f"{entry.number} will not be called again")


@router.get("/{number}", response_model=SuppressionEntry, dependencies=[RequireViewer])
async def check(number: str, queue: Queue) -> SuppressionEntry:
    entries = await _list(queue).all()
    key = normalise_number(number)
    if key not in entries:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{number} is not suppressed")
    return SuppressionEntry(number=key, reason=entries[key])


@router.delete("/{number}", response_model=Message, dependencies=[RequireAdmin])
async def unsuppress(number: str, queue: Queue) -> Message:
    if not await _list(queue).remove(number):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{number} is not suppressed")
    return Message(detail=f"{number} removed from the do-not-call list")
