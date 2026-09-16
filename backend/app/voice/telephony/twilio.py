"""Twilio telephony via the Calls REST API and bidirectional Media Streams.

The flow:

1. :meth:`TwilioTelephonyProvider.dial` creates a call whose TwiML opens a
   ``<Connect><Stream>`` back to this service's ``/ws/twilio/{call_id}``.
2. Twilio dials the customer; once answered it connects the websocket, and the
   API's websocket route hands it to :class:`MediaStreamRegistry`.
3. ``dial`` returns as soon as the stream attaches, so the runtime gets a live
   session; if nothing attaches before the timeout the call is treated as
   unanswered and the retry policy takes over.

This requires the API to be reachable from Twilio - set
``TWILIO_PUBLIC_BASE_URL`` to a public https/wss origin (a tunnel in dev).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
import uuid
from typing import Any

import httpx

from app.core.enums import FailureCategory
from app.voice.base import AudioChunk, DialRequest, TelephonyError
from app.voice.telephony.audio import FRAME_BYTES, FRAME_MS, chunk_frames, is_silence

logger = logging.getLogger(__name__)

API_ROOT = "https://api.twilio.com/2010-04-01"

# How long a caller may pause mid-sentence before we treat the turn as finished.
END_OF_SPEECH_SILENCE_MS = 700


class MediaStreamRegistry:
    """Rendezvous between an outbound dial and the websocket Twilio opens back.

    Also owns the "call is finished" signal. The websocket route has to stay
    inside its handler for as long as the session is using the socket - if it
    returned early Starlette would tear the connection down mid-call - so it
    waits on the event this registry hands it, and the session sets that event
    when it hangs up, transfers, or the stream ends.
    """

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[Any]] = {}
        self._finished: dict[str, asyncio.Event] = {}

    def expect(self, call_id: str) -> asyncio.Future[Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._waiters[call_id] = future
        return future

    def attach(self, call_id: str, websocket: Any) -> asyncio.Event | None:
        """Called by the websocket route.

        Returns the event to wait on, or ``None`` if no call was expecting
        this stream (a stale reconnect, or a call that already gave up).
        """
        future = self._waiters.pop(call_id, None)
        if future is None or future.done():
            return None
        finished = asyncio.Event()
        self._finished[call_id] = finished
        future.set_result(websocket)
        return finished

    def finish(self, call_id: str) -> None:
        """Release the websocket route; the session is done with the socket."""
        event = self._finished.pop(call_id, None)
        if event is not None:
            event.set()

    def cancel(self, call_id: str) -> None:
        future = self._waiters.pop(call_id, None)
        if future is not None and not future.done():
            future.cancel()
        self.finish(call_id)


registry = MediaStreamRegistry()


class TwilioCallSession:
    """A live Twilio call driven over its Media Stream websocket."""

    def __init__(
        self,
        call_id: str,
        external_id: str,
        websocket: Any,
        *,
        provider: TwilioTelephonyProvider,
    ) -> None:
        self.call_id = call_id
        self.external_id = external_id
        self._ws = websocket
        self._provider = provider
        self._stream_sid: str | None = None
        self._open = True
        self._inbound: asyncio.Queue[bytes] = asyncio.Queue()
        self._marks: asyncio.Queue[str] = asyncio.Queue()
        self._pump = asyncio.create_task(self._pump_inbound())

    @property
    def is_open(self) -> bool:
        return self._open

    async def _pump_inbound(self) -> None:
        """Demultiplex Twilio's websocket events into the audio/mark queues."""
        try:
            while True:
                event = json.loads(await self._ws.receive_text())
                kind = event.get("event")
                if kind == "start":
                    self._stream_sid = event["start"]["streamSid"]
                elif kind == "media":
                    await self._inbound.put(base64.b64decode(event["media"]["payload"]))
                elif kind == "mark":
                    await self._marks.put(event["mark"]["name"])
                elif kind == "stop":
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.info("twilio stream ended", extra={"call_id": self.call_id, "reason": str(exc)})
        finally:
            self._open = False
            self._provider.registry.finish(self.call_id)

    async def play(self, audio: AudioChunk) -> None:
        self._require_open()
        if not audio.data:
            return
        await self._await_stream_sid()
        for frame in chunk_frames(audio.data, FRAME_BYTES):
            await self._ws.send_text(
                json.dumps(
                    {
                        "event": "media",
                        "streamSid": self._stream_sid,
                        "media": {"payload": base64.b64encode(frame).decode()},
                    }
                )
            )
        mark = uuid.uuid4().hex[:8]
        await self._ws.send_text(
            json.dumps({"event": "mark", "streamSid": self._stream_sid, "mark": {"name": mark}})
        )
        # Twilio echoes the mark once the audio has actually been played out.
        deadline = time.monotonic() + (audio.duration_ms / 1000) + 5
        while time.monotonic() < deadline:
            try:
                if await asyncio.wait_for(self._marks.get(), timeout=1.0) == mark:
                    return
            except TimeoutError:
                if not self._open:
                    raise TelephonyError(
                        "call ended during playback",
                        category=FailureCategory.NETWORK,
                        provider="twilio",
                    ) from None

    async def listen(self, *, timeout_seconds: float = 8.0) -> AudioChunk:
        self._require_open()
        collected = bytearray()
        silent_ms = 0.0
        deadline = time.monotonic() + timeout_seconds

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                frame = await asyncio.wait_for(self._inbound.get(), timeout=remaining)
            except TimeoutError:
                break
            if is_silence(frame):
                # Only count trailing silence once the caller has started.
                if collected:
                    silent_ms += FRAME_MS
                    collected.extend(frame)
                    if silent_ms >= END_OF_SPEECH_SILENCE_MS:
                        break
                continue
            silent_ms = 0.0
            collected.extend(frame)

        if not self._open and not collected:
            raise TelephonyError(
                "call ended while waiting for the caller",
                category=FailureCategory.NETWORK,
                provider="twilio",
            )
        payload = bytes(collected)
        return AudioChunk(
            data=payload,
            duration_ms=(len(payload) / FRAME_BYTES) * FRAME_MS,
            sample_rate=8000,
            encoding="mulaw",
        )

    async def transfer(self, destination: str) -> None:
        self._require_open()
        twiml = f"<Response><Dial>{destination}</Dial></Response>"
        await self._provider.update_call(self.external_id, {"Twiml": twiml})
        await self._teardown()

    async def hangup(self, reason: str = "completed") -> None:
        if self._open:
            with contextlib.suppress(Exception):
                await self._provider.update_call(self.external_id, {"Status": "completed"})
        await self._teardown()

    async def _teardown(self) -> None:
        self._open = False
        self._pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._pump
        with contextlib.suppress(Exception):
            await self._ws.close()
        # Let the websocket route return now that nothing is using the socket.
        self._provider.registry.finish(self.call_id)

    async def _await_stream_sid(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while self._stream_sid is None and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        if self._stream_sid is None:
            raise TelephonyError(
                "media stream never sent a start event",
                category=FailureCategory.PROVIDER_ERROR,
                provider="twilio",
            )

    def _require_open(self) -> None:
        if not self._open:
            raise TelephonyError(
                "call is no longer open", category=FailureCategory.NETWORK, provider="twilio"
            )


class TwilioTelephonyProvider:
    name = "twilio"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        public_base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        stream_registry: MediaStreamRegistry | None = None,
    ) -> None:
        missing = [
            key
            for key, value in {
                "TWILIO_ACCOUNT_SID": account_sid,
                "TWILIO_AUTH_TOKEN": auth_token,
                "TWILIO_FROM_NUMBER": from_number,
                "TWILIO_PUBLIC_BASE_URL": public_base_url,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"twilio provider requires {', '.join(missing)}")
        self.account_sid = account_sid
        self.from_number = from_number
        self.public_base_url = public_base_url.rstrip("/")
        self.registry = stream_registry or registry
        self._client = client or httpx.AsyncClient(
            auth=(account_sid, auth_token), timeout=httpx.Timeout(20.0)
        )

    def _stream_url(self, call_id: str) -> str:
        origin = self.public_base_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{origin}/ws/twilio/{call_id}"

    async def dial(self, request: DialRequest) -> TwilioCallSession:
        waiter = self.registry.expect(request.call_id)
        twiml = (
            "<Response><Connect>"
            f'<Stream url="{self._stream_url(request.call_id)}" />'
            "</Connect></Response>"
        )
        try:
            response = await self._client.post(
                f"{API_ROOT}/Accounts/{self.account_sid}/Calls.json",
                data={
                    "To": request.to_number,
                    "From": request.from_number or self.from_number,
                    "Twiml": twiml,
                    "Timeout": str(int(request.timeout_seconds)),
                    "MachineDetection": "Enable",
                },
            )
            response.raise_for_status()
            external_id = response.json()["sid"]
        except httpx.HTTPStatusError as exc:
            self.registry.cancel(request.call_id)
            raise TelephonyError(
                f"twilio rejected the call: {exc.response.text[:200]}",
                category=_category_for_status(exc.response.status_code, exc.response.text),
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            self.registry.cancel(request.call_id)
            raise TelephonyError(
                f"twilio request failed: {exc}",
                category=FailureCategory.NETWORK,
                provider=self.name,
            ) from exc

        try:
            websocket = await asyncio.wait_for(waiter, timeout=request.timeout_seconds + 10)
        except (TimeoutError, asyncio.CancelledError) as exc:
            self.registry.cancel(request.call_id)
            with contextlib.suppress(Exception):
                await self.update_call(external_id, {"Status": "completed"})
            raise TelephonyError(
                "call was not answered", category=FailureCategory.NO_ANSWER, provider=self.name
            ) from exc

        return TwilioCallSession(request.call_id, external_id, websocket, provider=self)

    async def update_call(self, external_id: str, data: dict[str, str]) -> None:
        response = await self._client.post(
            f"{API_ROOT}/Accounts/{self.account_sid}/Calls/{external_id}.json", data=data
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()


def _category_for_status(status: int, body: str) -> FailureCategory:
    if status == 429:
        return FailureCategory.RATE_LIMITED
    if status >= 500:
        return FailureCategory.PROVIDER_ERROR
    # 21211/21217 are Twilio's "invalid 'To' number" codes.
    if any(code in body for code in ("21211", "21217", "21214")):
        return FailureCategory.INVALID_NUMBER
    return FailureCategory.AGENT_CONFIG
