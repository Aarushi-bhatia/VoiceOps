"""Twilio media-stream plumbing that can be exercised without a phone line."""

from __future__ import annotations

import asyncio

import pytest

from app.voice.telephony.audio import (
    FRAME_BYTES,
    average_amplitude,
    chunk_frames,
    is_silence,
    ulaw_to_linear,
)
from app.voice.telephony.twilio import MediaStreamRegistry


def test_ulaw_silence_and_speech_are_distinguished():
    silence = b"\xff" * FRAME_BYTES
    speech = bytes(range(0, 160))
    assert is_silence(silence)
    assert not is_silence(speech)
    assert average_amplitude(silence) < average_amplitude(speech)


def test_ulaw_decodes_to_signed_linear_samples():
    samples = ulaw_to_linear(b"\xff\x7f")
    assert len(samples) == 2
    # 0xFF and 0x7F are the positive and negative zero-ish codes.
    assert samples[0] == -samples[1]


def test_frames_are_fixed_size_and_padded_with_silence():
    frames = list(chunk_frames(b"\x10" * 250))
    assert len(frames) == 2
    assert all(len(frame) == FRAME_BYTES for frame in frames)
    assert frames[1].endswith(b"\xff")


async def test_registry_hands_the_socket_to_the_waiting_call():
    registry = MediaStreamRegistry()
    waiter = registry.expect("call-1")

    finished = registry.attach("call-1", "socket")
    assert finished is not None
    assert await asyncio.wait_for(waiter, timeout=1) == "socket"

    # The route waits on this until the session is done with the socket.
    assert not finished.is_set()
    registry.finish("call-1")
    assert finished.is_set()


async def test_registry_rejects_a_stream_nobody_asked_for():
    registry = MediaStreamRegistry()
    assert registry.attach("unknown", "socket") is None


async def test_cancelling_a_dial_releases_any_waiter():
    registry = MediaStreamRegistry()
    waiter = registry.expect("call-2")
    registry.cancel("call-2")

    assert waiter.cancelled()
    # A late stream for that call is refused rather than left hanging.
    assert registry.attach("call-2", "socket") is None


def test_twilio_provider_requires_its_credentials():
    from app.voice.telephony.twilio import TwilioTelephonyProvider

    with pytest.raises(ValueError, match="TWILIO_PUBLIC_BASE_URL"):
        TwilioTelephonyProvider(
            account_sid="sid", auth_token="token", from_number="+1555", public_base_url=""
        )


def test_stream_url_is_derived_from_the_public_origin():
    from app.voice.telephony.twilio import TwilioTelephonyProvider

    provider = TwilioTelephonyProvider(
        account_sid="sid",
        auth_token="token",
        from_number="+1555",
        public_base_url="https://voiceops.example.com/",
    )
    assert provider._stream_url("abc") == "wss://voiceops.example.com/ws/twilio/abc"
