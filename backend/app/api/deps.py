"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.queue.call_queue import CallQueue, get_queue
from app.services.calls import CallService

DbSession = Annotated[AsyncSession, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Queue = Annotated[CallQueue, Depends(get_queue)]


async def get_call_service(session: DbSession, queue: Queue) -> AsyncIterator[CallService]:
    yield CallService(session, queue)


Calls = Annotated[CallService, Depends(get_call_service)]
