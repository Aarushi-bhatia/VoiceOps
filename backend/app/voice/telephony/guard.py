"""Spend and safety limits that apply to every real telephony provider.

Wrapping the provider, rather than building the checks into each carrier, means
a new carrier gets them for free and they cannot be forgotten.
"""

from __future__ import annotations

import dataclasses
import re

from app.core.enums import FailureCategory
from app.voice.base import CallSession, DialRequest, TelephonyError, TelephonyProvider


def normalise_number(number: str) -> str:
    """Strip formatting so ``+1 (555) 010-0100`` matches ``+15550100100``."""
    return re.sub(r"[^\d+]", "", number)


class GuardedTelephony:
    """Refuses numbers outside an allowlist and caps how long a call may run.

    Pointing a development or demo environment at a trial account can still
    reach real people, and a stuck call keeps billing. An empty allowlist or a
    zero limit disables the respective check.
    """

    def __init__(
        self,
        inner: TelephonyProvider,
        *,
        allowed_numbers: frozenset[str] = frozenset(),
        max_call_seconds: int = 0,
    ) -> None:
        self._inner = inner
        self.name = inner.name
        self._allowed = frozenset(normalise_number(n) for n in allowed_numbers)
        self._max_call_seconds = max_call_seconds

    async def dial(self, request: DialRequest) -> CallSession:
        if self._allowed and normalise_number(request.to_number) not in self._allowed:
            # Permanent: retrying will not make a blocked number allowed.
            raise TelephonyError(
                f"{request.to_number} is not in TELEPHONY_ALLOWED_NUMBERS",
                category=FailureCategory.INVALID_NUMBER,
                provider=self.name,
            )
        if self._max_call_seconds and request.max_duration_seconds is None:
            request = dataclasses.replace(request, max_duration_seconds=self._max_call_seconds)
        return await self._inner.dial(request)

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()
