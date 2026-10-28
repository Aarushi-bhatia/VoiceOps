"""Telnyx adapter driven end to end against a fake carrier (no network, no phone)."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from app.core.enums import FailureCategory
from app.voice.base import AudioChunk, DialRequest, TelephonyError
from app.voice.telephony.stream import MediaStreamRegistry
from app.voice.telephony.telnyx import TelnyxTelephonyProvider


class FakeTelnyxSocket:
    """The websocket Telnyx opens back, from the service's side of it."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def receive_text(self) -> str:
        return await self.incoming.get()

    async def send_text(self, text: str) -> None:
        message = json.loads(text)
        self.sent.append(message)
        if message["event"] == "mark":
            # Telnyx echoes a mark once the audio before it has played out.
            await self.push({"event": "mark", "mark": message["mark"]})

    async def close(self) -> None:
        self.closed = True

    async def push(self, event: dict) -> None:
        await self.incoming.put(json.dumps(event))


class FakeTelnyxApi:
    def __init__(self, *, status: int = 200, body: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = status
        self.body = body if body is not None else {"data": {"call_control_id": "cc-1"}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(self.status, json=self.body)


def make_provider(
    api: FakeTelnyxApi, stream_registry: MediaStreamRegistry
) -> TelnyxTelephonyProvider:
    return TelnyxTelephonyProvider(
        api_key="key",
        connection_id="conn-1",
        from_number="+15550100",
        public_base_url="https://voiceops.example.com/",
        client=httpx.AsyncClient(
            base_url="https://api.telnyx.com/v2",
            transport=httpx.MockTransport(api.handler),
        ),
        stream_registry=stream_registry,
    )


def speech(ms: int = 100) -> bytes:
    return bytes(range(160)) * (ms // 20)


async def answered_session(provider, stream_registry, socket, **request_kwargs):
    """Dial, then have Telnyx connect and announce the stream, as it does on answer."""
    dialling = asyncio.create_task(
        provider.dial(DialRequest(call_id="call-1", to_number="+15550100199", **request_kwargs))
    )
    while "call-1" not in stream_registry._waiters:
        await asyncio.sleep(0)
    stream_registry.attach("call-1", socket)
    await socket.push({"event": "connected", "version": "1.0.0"})
    await socket.push({"event": "start", "stream_id": "s-1", "start": {}})
    return await asyncio.wait_for(dialling, timeout=2)


def test_provider_requires_its_credentials():
    with pytest.raises(ValueError, match="TELNYX_CONNECTION_ID"):
        TelnyxTelephonyProvider("key", "", "+1555", "https://voiceops.example.com")


def test_stream_url_uses_the_carrier_neutral_path():
    provider = TelnyxTelephonyProvider("key", "conn", "+1555", "https://voiceops.example.com/")
    assert provider._stream_url("abc") == "wss://voiceops.example.com/ws/media/abc"


async def test_dial_places_a_bidirectional_pcmu_call_and_returns_the_session():
    api, stream_registry, socket = FakeTelnyxApi(), MediaStreamRegistry(), FakeTelnyxSocket()
    provider = make_provider(api, stream_registry)

    session = await answered_session(provider, stream_registry, socket, max_duration_seconds=120)

    path, body = api.calls[0]
    assert path == "/v2/calls"
    assert body["connection_id"] == "conn-1"
    assert body["to"] == "+15550100199"
    assert body["from"] == "+15550100"
    assert body["stream_url"] == "wss://voiceops.example.com/ws/media/call-1"
    assert body["stream_bidirectional_mode"] == "rtp"
    assert body["stream_bidirectional_codec"] == "PCMU"
    assert body["time_limit_secs"] == 120
    assert session.external_id == "cc-1"
    await session.hangup()


async def test_play_sends_the_audio_then_waits_for_the_carrier_to_finish_it():
    api, stream_registry, socket = FakeTelnyxApi(), MediaStreamRegistry(), FakeTelnyxSocket()
    session = await answered_session(make_provider(api, stream_registry), stream_registry, socket)

    audio = speech(60)
    await session.play(AudioChunk(data=audio, duration_ms=60, encoding="mulaw"))

    media = [m for m in socket.sent if m["event"] == "media"]
    assert b"".join(base64.b64decode(m["media"]["payload"]) for m in media) == audio
    # Telnyx addresses the stream by the socket itself, with no stream id on the frame.
    assert all("streamSid" not in m for m in socket.sent)
    assert socket.sent[-1]["event"] == "mark"
    await session.hangup()


async def test_listen_collects_the_caller_and_stops_after_they_pause():
    api, stream_registry, socket = FakeTelnyxApi(), MediaStreamRegistry(), FakeTelnyxSocket()
    session = await answered_session(make_provider(api, stream_registry), stream_registry, socket)

    spoken = speech(40)
    for frame in (spoken[:160], spoken[160:]):
        await socket.push(
            {
                "event": "media",
                "media": {"track": "inbound", "payload": base64.b64encode(frame).decode()},
            }
        )
    # Our own audio echoed back must not be mistaken for the caller.
    await socket.push(
        {
            "event": "media",
            "media": {"track": "outbound", "payload": base64.b64encode(speech(20)).decode()},
        }
    )
    for _ in range(40):  # 800 ms of silence ends the turn
        await socket.push(
            {
                "event": "media",
                "media": {"track": "inbound", "payload": base64.b64encode(b"\xff" * 160).decode()},
            }
        )

    heard = await session.listen(timeout_seconds=2)

    assert heard.data.startswith(spoken)
    assert heard.encoding == "mulaw"
    await session.hangup()


async def test_hangup_ends_the_call_through_the_rest_api():
    api, stream_registry, socket = FakeTelnyxApi(), MediaStreamRegistry(), FakeTelnyxSocket()
    session = await answered_session(make_provider(api, stream_registry), stream_registry, socket)

    await session.hangup()

    assert api.calls[-1] == ("/v2/calls/cc-1/actions/hangup", {})
    assert socket.closed
    assert not session.is_open


async def test_transfer_hands_the_call_to_the_destination():
    api, stream_registry, socket = FakeTelnyxApi(), MediaStreamRegistry(), FakeTelnyxSocket()
    session = await answered_session(make_provider(api, stream_registry), stream_registry, socket)

    await session.transfer("+15550100777")

    assert api.calls[-1] == ("/v2/calls/cc-1/actions/transfer", {"to": "+15550100777"})
    assert not session.is_open


async def test_an_unanswered_call_is_hung_up_and_retried_as_no_answer():
    api, stream_registry = FakeTelnyxApi(), MediaStreamRegistry()
    provider = make_provider(api, stream_registry)

    # The stream wait is the ring timeout plus 10 s; -9.9 makes that 100 ms.
    with pytest.raises(TelephonyError) as exc:
        await provider.dial(
            DialRequest(call_id="call-2", to_number="+15550100199", timeout_seconds=-9.9)
        )

    assert exc.value.category is FailureCategory.NO_ANSWER
    assert api.calls[-1][0] == "/v2/calls/cc-1/actions/hangup"
    # A stream arriving late is refused rather than left hanging.
    assert stream_registry.attach("call-2", FakeTelnyxSocket()) is None


async def test_the_call_is_not_answered_until_the_stream_starts():
    # Telnyx opens the socket while the phone is still ringing and sends the
    # start event only on answer; the socket attaching must not count as one.
    api, stream_registry, socket = FakeTelnyxApi(), MediaStreamRegistry(), FakeTelnyxSocket()
    provider = make_provider(api, stream_registry)

    dialling = asyncio.create_task(
        provider.dial(DialRequest(call_id="call-1", to_number="+15550100199"))
    )
    while "call-1" not in stream_registry._waiters:
        await asyncio.sleep(0)
    stream_registry.attach("call-1", socket)
    await socket.push({"event": "connected", "version": "1.0.0"})

    await asyncio.sleep(0.1)
    assert not dialling.done()

    await socket.push({"event": "start", "stream_id": "s-1", "start": {}})
    session = await asyncio.wait_for(dialling, timeout=2)
    assert session.is_open
    await session.hangup()


async def test_a_ringing_call_that_never_starts_is_no_answer():
    api, stream_registry, socket = FakeTelnyxApi(), MediaStreamRegistry(), FakeTelnyxSocket()
    provider = make_provider(api, stream_registry)

    dialling = asyncio.create_task(
        provider.dial(DialRequest(call_id="call-3", to_number="+15550100199", timeout_seconds=0.2))
    )
    while "call-3" not in stream_registry._waiters:
        await asyncio.sleep(0)
    stream_registry.attach("call-3", socket)
    await socket.push({"event": "connected", "version": "1.0.0"})

    with pytest.raises(TelephonyError) as exc:
        await asyncio.wait_for(dialling, timeout=2)

    assert exc.value.category is FailureCategory.NO_ANSWER
    assert api.calls[-1][0] == "/v2/calls/cc-1/actions/hangup"
    assert socket.closed


@pytest.mark.parametrize(
    ("status", "body", "category"),
    [
        (429, {"errors": [{"title": "rate limit"}]}, FailureCategory.RATE_LIMITED),
        (503, {"errors": []}, FailureCategory.PROVIDER_ERROR),
        (
            422,
            {"errors": [{"detail": "Invalid destination number"}]},
            FailureCategory.INVALID_NUMBER,
        ),
        (401, {"errors": [{"title": "Authentication failed"}]}, FailureCategory.AGENT_CONFIG),
    ],
)
async def test_rejections_map_onto_the_retry_categories(status, body, category):
    api = FakeTelnyxApi(status=status, body=body)
    stream_registry = MediaStreamRegistry()
    provider = make_provider(api, stream_registry)

    with pytest.raises(TelephonyError) as exc:
        await provider.dial(DialRequest(call_id="call-3", to_number="+15550100199"))

    assert exc.value.category is category
    assert exc.value.provider == "telnyx"
    # The rendezvous is released so nothing leaks after a rejected call.
    assert "call-3" not in stream_registry._waiters
