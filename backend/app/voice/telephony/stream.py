"""Shared plumbing for carriers that dial over REST and stream audio over websocket.

Twilio and Telnyx both work the same way: a REST call places the call, and once
it is answered the carrier opens a websocket back to this service carrying 8 kHz
mu-law audio in JSON frames. They differ only in field names and endpoints, so
:class:`MediaStreamProvider` owns the dial / answer / timeout flow and
:class:`MediaStreamSession` owns turn-taking, and a carrier supplies the few
hooks that say how it spells things. A new websocket carrier is one small module
that subclasses both and registers itself (see ``telnyx.py``).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
import uuid
from typing import Any, ClassVar

import httpx

from app.core.enums import FailureCategory
from app.voice.base import AudioChunk, DialRequest, TelephonyError
from app.voice.telephony.audio import FRAME_BYTES, FRAME_MS, chunk_frames, is_silence

logger = logging.getLogger(__name__)

# How long a caller may pause mid-sentence before we treat the turn as finished.
END_OF_SPEECH_SILENCE_MS = 700


class MediaStreamRegistry:
    """Rendezvous between an outbound dial and the websocket the carrier opens back.

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


class MediaStreamSession:
    """A live call driven over a carrier's media-stream websocket.

    Subclasses say how their carrier spells a stream id and the outbound media
    and mark messages. Ending and transferring a call go through the provider,
    which already holds the REST client.
    """

    provider_name: ClassVar[str] = "unknown"
    # Bytes per outbound websocket message. Twilio takes 20 ms frames; carriers
    # that accept longer chunks can send fewer, larger messages.
    outbound_chunk_bytes: ClassVar[int] = FRAME_BYTES

    def __init__(
        self,
        call_id: str,
        external_id: str,
        websocket: Any,
        *,
        provider: MediaStreamProvider,
    ) -> None:
        self.call_id = call_id
        self.external_id = external_id
        self._ws = websocket
        self._provider = provider
        self._stream_started = False
        self._stream_sid: str | None = None
        self._open = True
        self._inbound: asyncio.Queue[bytes] = asyncio.Queue()
        self._marks: asyncio.Queue[str] = asyncio.Queue()
        self._pump = asyncio.create_task(self._pump_inbound())

    # ---- how this carrier spells things ----

    def _stream_id(self, event: dict[str, Any]) -> str | None:
        """The stream id carried by a ``start`` event, if the carrier has one."""
        raise NotImplementedError

    def _media_message(self, payload: str) -> dict[str, Any]:
        raise NotImplementedError

    def _mark_message(self, name: str) -> dict[str, Any]:
        raise NotImplementedError

    # ---- the session ----

    @property
    def is_open(self) -> bool:
        return self._open

    async def _pump_inbound(self) -> None:
        """Demultiplex the carrier's websocket events into the audio/mark queues."""
        try:
            while True:
                event = json.loads(await self._ws.receive_text())
                kind = event.get("event")
                if kind == "start":
                    self._stream_sid = self._stream_id(event)
                    self._stream_started = True
                elif kind == "media":
                    media = event["media"]
                    # The caller's side only; a carrier may echo our own audio.
                    if media.get("track", "inbound") != "outbound":
                        await self._inbound.put(base64.b64decode(media["payload"]))
                elif kind == "mark":
                    await self._marks.put(event["mark"]["name"])
                elif kind == "stop":
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.info(
                "media stream ended",
                extra={"call_id": self.call_id, "provider": self.provider_name, "reason": str(exc)},
            )
        finally:
            self._open = False
            self._provider.registry.finish(self.call_id)

    async def play(self, audio: AudioChunk) -> None:
        self._require_open()
        if not audio.data:
            return
        await self._await_stream_start()
        # Pad to whole 20 ms frames, then cut into this carrier's message size.
        padded = b"".join(chunk_frames(audio.data))
        step = self.outbound_chunk_bytes
        for offset in range(0, len(padded), step):
            payload = base64.b64encode(padded[offset : offset + step]).decode()
            await self._ws.send_text(json.dumps(self._media_message(payload)))
        mark = uuid.uuid4().hex[:8]
        await self._ws.send_text(json.dumps(self._mark_message(mark)))
        # The carrier echoes the mark once the audio has actually been played out.
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
                        provider=self.provider_name,
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
                    silent_ms += (len(frame) / FRAME_BYTES) * FRAME_MS
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
                provider=self.provider_name,
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
        await self._provider.transfer_call(self.external_id, destination)
        await self._teardown()

    async def hangup(self, reason: str = "completed") -> None:
        if self._open:
            with contextlib.suppress(Exception):
                await self._provider.hangup_call(self.external_id)
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

    async def _await_stream_start(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not self._stream_started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        if not self._stream_started:
            raise TelephonyError(
                "media stream never sent a start event",
                category=FailureCategory.PROVIDER_ERROR,
                provider=self.provider_name,
            )

    def _require_open(self) -> None:
        if not self._open:
            raise TelephonyError(
                "call is no longer open",
                category=FailureCategory.NETWORK,
                provider=self.provider_name,
            )


class MediaStreamProvider:
    """Dials over REST and hands back a session once the stream attaches.

    A carrier implements :meth:`_place_call`, :meth:`hangup_call`,
    :meth:`transfer_call` and :meth:`_classify_error`; the flow around them -
    waiting for the answer, treating silence as no-answer, mapping failures onto
    the retry categories - is shared.
    """

    name: ClassVar[str] = "unknown"
    session_class: ClassVar[type[MediaStreamSession]]
    # Path the carrier connects back to; the call id is appended.
    stream_path: ClassVar[str] = "/ws/media"

    def __init__(
        self,
        public_base_url: str,
        client: httpx.AsyncClient,
        stream_registry: MediaStreamRegistry | None = None,
    ) -> None:
        self.public_base_url = public_base_url.rstrip("/")
        self.registry = stream_registry or registry
        self._client = client

    @staticmethod
    def require(provider: str, **settings: str) -> None:
        """Fail at construction, naming the variables, rather than mid-call."""
        missing = [key for key, value in settings.items() if not value]
        if missing:
            raise ValueError(f"{provider} provider requires {', '.join(missing)}")

    def _stream_url(self, call_id: str) -> str:
        origin = self.public_base_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{origin}{self.stream_path}/{call_id}"

    async def _place_call(self, request: DialRequest, stream_url: str) -> str:
        """Create the call and return the carrier's id for it."""
        raise NotImplementedError

    async def hangup_call(self, external_id: str) -> None:
        raise NotImplementedError

    async def transfer_call(self, external_id: str, destination: str) -> None:
        raise NotImplementedError

    def _classify_error(self, status: int, body: str) -> FailureCategory:
        if status == 429:
            return FailureCategory.RATE_LIMITED
        if status >= 500:
            return FailureCategory.PROVIDER_ERROR
        return FailureCategory.AGENT_CONFIG

    async def dial(self, request: DialRequest) -> MediaStreamSession:
        waiter = self.registry.expect(request.call_id)
        try:
            external_id = await self._place_call(request, self._stream_url(request.call_id))
        except httpx.HTTPStatusError as exc:
            self.registry.cancel(request.call_id)
            raise TelephonyError(
                f"{self.name} rejected the call: {exc.response.text[:200]}",
                category=self._classify_error(exc.response.status_code, exc.response.text),
                provider=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            self.registry.cancel(request.call_id)
            raise TelephonyError(
                f"{self.name} request failed: {exc}",
                category=FailureCategory.NETWORK,
                provider=self.name,
            ) from exc

        try:
            websocket = await asyncio.wait_for(waiter, timeout=request.timeout_seconds + 10)
        except (TimeoutError, asyncio.CancelledError) as exc:
            self.registry.cancel(request.call_id)
            with contextlib.suppress(Exception):
                await self.hangup_call(external_id)
            raise TelephonyError(
                "call was not answered", category=FailureCategory.NO_ANSWER, provider=self.name
            ) from exc

        return self.session_class(request.call_id, external_id, websocket, provider=self)

    async def aclose(self) -> None:
        await self._client.aclose()
