"""Simulated telephony: places calls against a scripted virtual customer.

Behaviour is deterministic for a given ``(call_id, mock_seed)`` so runs and
tests replay identically. A few number suffixes are reserved as fixtures so the
retry machinery can be demonstrated without waiting for a random failure:

======================  ==================================================
number ends with        behaviour
======================  ==================================================
``1111``                always answers - no random failures (tests, demos)
``0000``                invalid number - permanent failure, no retries
``9999``                never answers - retries until attempts are exhausted
``8888``                busy for the first two attempts, answers on the third
``7777``                answers, then the line drops mid-conversation
anything else           ~8% no answer, ~3% busy, otherwise answered
======================  ==================================================
"""

from __future__ import annotations

import asyncio
import random
import uuid
import zlib

from app.core.enums import FailureCategory
from app.voice.base import AudioChunk, DialRequest, TelephonyError
from app.voice.tts.mock import speech_duration_ms

_AFFIRM = ["Yes, that's right.", "Yep, correct.", "That's the one."]
_DENY = ["No, that's not right.", "Nope.", "That isn't correct."]

# Goals a simulated customer can call about. The vocabulary here is what the
# agent's branch classifier has to work with.
GOALS: list[dict[str, str]] = [
    {
        "intent": "refund",
        "opening": "I'd like a refund for an order that arrived damaged.",
        "followup": "The item was broken in the box, so I want my money back.",
    },
    {
        "intent": "delivery_status",
        "opening": "I'm calling about a delivery that hasn't shown up yet.",
        "followup": "I just want to know where my package is and when it arrives.",
    },
    {
        "intent": "cancel_order",
        "opening": "I need to cancel an order I placed this morning.",
        "followup": "Please cancel it before it ships.",
    },
    {
        "intent": "billing",
        "opening": "There's a charge on my invoice I don't recognise.",
        "followup": "I was billed twice for the same subscription payment.",
    },
]


class SimulatedCustomer:
    """Generates replies to whatever the agent last said."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.goal = self.rng.choice(GOALS)
        self.order_id = f"ORD-{self.rng.randint(10000, 99999)}"
        self.email = f"customer{self.rng.randint(100, 999)}@example.com"
        self.phone = f"+1555{self.rng.randint(1000000, 9999999)}"
        self.patience = self.rng.randint(6, 14)
        self.cooperative = self.rng.random() > 0.15
        self.turns = 0

    def reply(self, agent_text: str) -> str:
        """Return what the customer says next. Empty string means silence."""
        self.turns += 1
        text = agent_text.lower()

        # Occasional real-world noise: silence, or a request to repeat.
        roll = self.rng.random()
        if roll < 0.04:
            return ""
        if roll < 0.08:
            return "Sorry, what was that? Could you repeat it?"

        if any(
            k in text for k in ("order number", "order id", "reference number", "order reference")
        ):
            return f"It's {self.order_id}."
        if "email" in text:
            return f"Sure, it's {self.email}."
        if any(k in text for k in ("phone number", "contact number", "callback number")):
            return f"You can reach me on {self.phone}."
        if any(k in text for k in ("anything else", "is there anything more")):
            return "No, that's everything. Thanks."
        if any(k in text for k in ("hold", "transfer", "connect you", "put you through")):
            return "Okay, that's fine."
        if any(
            k in text
            for k in (
                "how can i help",
                "what can i do",
                "calling about",
                "reason for your call",
                "what brings you",
            )
        ):
            return self.goal["opening"]
        if self._is_yes_no(text):
            positive = self.cooperative or self.rng.random() < 0.6
            return self.rng.choice(_AFFIRM if positive else _DENY)
        if self.turns > self.patience and not self.cooperative:
            return "Look, I've been on this call a while. Can I speak to a person?"
        if self.turns == 1:
            return self.goal["opening"]
        return self.goal["followup"]

    @staticmethod
    def _is_yes_no(text: str) -> bool:
        return any(
            k in text
            for k in (
                "is that correct",
                "can i confirm",
                "would you like",
                "do you want",
                "shall i",
                "should i",
                "are you",
                "did you",
                "confirm that",
                "sound good",
                "is that right",
            )
        )


class MockCallSession:
    """A simulated answered call."""

    def __init__(
        self,
        call_id: str,
        customer: SimulatedCustomer,
        *,
        latency_scale: float = 1.0,
        drops_after_turn: int | None = None,
    ) -> None:
        self.call_id = call_id
        self.external_id = f"mock-{uuid.uuid4().hex[:12]}"
        self.customer = customer
        self.latency_scale = latency_scale
        self.drops_after_turn = drops_after_turn
        self._open = True
        self._last_agent_text = ""
        self._turns = 0

    @property
    def is_open(self) -> bool:
        return self._open

    async def play(self, audio: AudioChunk) -> None:
        self._require_open()
        self._last_agent_text = audio.text_hint or ""
        # Playback is real time on a phone line; compress it for the mock.
        await asyncio.sleep(min(audio.duration_ms / 1000, 2.0) * 0.05 * self.latency_scale)

    async def listen(self, *, timeout_seconds: float = 8.0) -> AudioChunk:
        self._require_open()
        self._turns += 1
        if self.drops_after_turn is not None and self._turns > self.drops_after_turn:
            self._open = False
            raise TelephonyError(
                "the line dropped mid-conversation",
                category=FailureCategory.NETWORK,
                provider="mock",
            )
        text = self.customer.reply(self._last_agent_text)
        await asyncio.sleep(min(0.3 + len(text) * 0.004, 1.5) * 0.1 * self.latency_scale)
        return AudioChunk(
            data=b"" if not text else b"\x00" * 256,
            duration_ms=speech_duration_ms(text) if text else 0.0,
            text_hint=text or None,
        )

    async def transfer(self, destination: str) -> None:
        self._require_open()
        await asyncio.sleep(0.05 * self.latency_scale)
        self._open = False

    async def hangup(self, reason: str = "completed") -> None:
        self._open = False

    def _require_open(self) -> None:
        if not self._open:
            raise TelephonyError(
                "call is no longer open", category=FailureCategory.NETWORK, provider="mock"
            )


class MockTelephonyProvider:
    name = "mock"

    def __init__(self, seed: int = 1337, latency_scale: float = 1.0) -> None:
        self.seed = seed
        self.latency_scale = latency_scale

    async def dial(self, request: DialRequest) -> MockCallSession:
        number = request.to_number
        # Stable per-call randomness: same call id -> same simulated customer.
        rng = random.Random(f"{self.seed}:{request.call_id}:{request.attempt}")
        # zlib.crc32 (not hash()) so the persona is stable across processes,
        # since Python randomises string hashing per interpreter run.
        customer = SimulatedCustomer(seed=zlib.crc32(f"{self.seed}:{request.call_id}".encode()))

        await asyncio.sleep(min(0.6, request.timeout_seconds) * 0.15 * self.latency_scale)

        if number.endswith("0000"):
            raise TelephonyError(
                f"{number} is not a valid destination",
                category=FailureCategory.INVALID_NUMBER,
                provider=self.name,
            )
        if number.endswith("9999"):
            raise TelephonyError(
                "no answer", category=FailureCategory.NO_ANSWER, provider=self.name
            )
        if number.endswith("8888") and request.attempt < 2:
            raise TelephonyError("line is busy", category=FailureCategory.BUSY, provider=self.name)

        roll = 1.0 if number.endswith("1111") else rng.random()
        if roll < 0.08:
            raise TelephonyError(
                "no answer", category=FailureCategory.NO_ANSWER, provider=self.name
            )
        if roll < 0.11:
            raise TelephonyError("line is busy", category=FailureCategory.BUSY, provider=self.name)

        return MockCallSession(
            call_id=request.call_id,
            customer=customer,
            latency_scale=self.latency_scale,
            drops_after_turn=3 if number.endswith("7777") else None,
        )
