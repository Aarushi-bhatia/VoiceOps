"""Live event stream for the dashboard, and Twilio's media-stream socket."""

from __future__ import annotations

import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.queue.call_queue import get_queue

logger = logging.getLogger(__name__)
router = APIRouter(tags=["events"])


@router.websocket("/ws/events")
async def call_events(websocket: WebSocket) -> None:
    """Fan out queue and call events published by the workers.

    Messages are the JSON documents written by ``CallQueue.publish_event``:
    ``{type, call_id, agent_id, status, payload, ts}``.
    """
    await websocket.accept()
    queue = get_queue()
    stream = queue.listen_events()
    try:
        async for message in stream:
            await websocket.send_text(message)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - never take the API down for one socket
        logger.info("event socket closed", extra={"error": str(exc)})
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()


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
    # signals that it has finished with it.
    try:
        await finished.wait()
    finally:
        registry.finish(call_id)
