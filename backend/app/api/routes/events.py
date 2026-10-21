"""Twilio's media-stream socket."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from typing import Any

from fastapi import APIRouter, WebSocket

logger = logging.getLogger(__name__)
router = APIRouter(tags=["events"])

# Set when the application is shutting down. The media-stream handler races the
# end of its call against this, so it returns promptly instead of blocking a
# graceful shutdown while it waits for a call that may never finish.
_shutting_down = asyncio.Event()
_previous_handlers: dict[int, Any] = {}


def begin_shutdown() -> None:
    _shutting_down.set()


def reset_shutdown() -> None:
    """Called on startup so a restart in the same process starts clean."""
    _shutting_down.clear()


def install_shutdown_signal_handlers() -> None:
    """Learn about SIGTERM/SIGINT before the server starts draining connections.

    Uvicorn's graceful shutdown waits for open connections to finish *before*
    it runs lifespan shutdown. A long-lived websocket that blocks until the app
    tells it to stop therefore deadlocks: the signal to stop only arrives after
    the connection it is waiting on has closed. Chaining onto the signal
    handler - rather than relying on lifespan - breaks that cycle.

    Signals can only be installed on the main thread; under a test client or an
    embedded server this is a no-op and the lifespan path still applies.
    """
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.getsignal(signum)
        except (ValueError, OSError):  # pragma: no cover - platform dependent
            continue

        def handler(received: int, frame: Any, _previous: Any = previous) -> None:
            begin_shutdown()
            if callable(_previous):
                _previous(received, frame)

        try:
            signal.signal(signum, handler)
        except (ValueError, OSError):
            continue  # not the main thread
        _previous_handlers[signum] = previous


def restore_shutdown_signal_handlers() -> None:
    for signum, previous in _previous_handlers.items():
        with contextlib.suppress(ValueError, OSError, TypeError):
            signal.signal(signum, previous)
    _previous_handlers.clear()


@router.websocket("/ws/twilio/{call_id}")
async def twilio_media_stream(websocket: WebSocket, call_id: str) -> None:
    """Attach an inbound Twilio Media Stream to the call that is waiting for it."""
    from app.voice.telephony.twilio import registry

    await websocket.accept()
    finished = registry.attach(call_id, websocket)
    if finished is None:
        logger.warning("media stream arrived with nothing waiting", extra={"call_id": call_id})
        await websocket.close(code=1011, reason="no call is waiting for this stream")
        return

    # TwilioCallSession owns the socket from here; returning would make Starlette
    # tear the connection down mid-call, so hold the route open until the session
    # signals that it has finished with it - or until the app shuts down.
    stopping = asyncio.create_task(_shutting_down.wait(), name="twilio-shutdown-watch")
    waiter = asyncio.create_task(finished.wait(), name="twilio-finished")
    try:
        await asyncio.wait({waiter, stopping}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (waiter, stopping):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        registry.finish(call_id)
