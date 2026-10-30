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

from typing import Any

import httpx
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings
from app.core.enums import FailureCategory
from app.voice.base import DialRequest
from app.voice.telephony.providers import ProviderSettings, register
from app.voice.telephony.stream import (
    END_OF_SPEECH_SILENCE_MS,
    MediaStreamProvider,
    MediaStreamRegistry,
    MediaStreamSession,
    registry,
)

__all__ = [
    "END_OF_SPEECH_SILENCE_MS",
    "MediaStreamRegistry",
    "TwilioCallSession",
    "TwilioSettings",
    "TwilioTelephonyProvider",
    "registry",
]

API_ROOT = "https://api.twilio.com/2010-04-01"


class TwilioSettings(ProviderSettings):
    model_config = SettingsConfigDict(env_prefix="TWILIO_", **ProviderSettings.base_config)

    account_sid: str = ""
    auth_token: str = ""
    from_number: str = ""
    # Public origin Twilio can reach for Media Streams (a tunnel in dev).
    public_base_url: str = ""


class TwilioCallSession(MediaStreamSession):
    """A live Twilio call driven over its Media Stream websocket."""

    provider_name = "twilio"

    def _stream_id(self, event: dict[str, Any]) -> str | None:
        return event["start"]["streamSid"]

    def _media_message(self, payload: str) -> dict[str, Any]:
        return {"event": "media", "streamSid": self._stream_sid, "media": {"payload": payload}}

    def _mark_message(self, name: str) -> dict[str, Any]:
        return {"event": "mark", "streamSid": self._stream_sid, "mark": {"name": name}}


class TwilioTelephonyProvider(MediaStreamProvider):
    name = "twilio"
    session_class = TwilioCallSession
    stream_path = "/ws/twilio"

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
        self.require(
            "twilio",
            TWILIO_ACCOUNT_SID=account_sid,
            TWILIO_AUTH_TOKEN=auth_token,
            TWILIO_FROM_NUMBER=from_number,
            TWILIO_PUBLIC_BASE_URL=public_base_url,
        )
        super().__init__(
            public_base_url,
            client
            or httpx.AsyncClient(auth=(account_sid, auth_token), timeout=httpx.Timeout(20.0)),
            stream_registry,
        )
        self.account_sid = account_sid
        self.from_number = from_number

    async def _place_call(self, request: DialRequest, stream_url: str) -> str:
        twiml = f'<Response><Connect><Stream url="{stream_url}" /></Connect></Response>'
        data = {
            "To": request.to_number,
            "From": request.from_number or self.from_number,
            "Twiml": twiml,
            "Timeout": str(int(request.timeout_seconds)),
            "MachineDetection": "Enable",
        }
        if request.max_duration_seconds:
            data["TimeLimit"] = str(request.max_duration_seconds)
        response = await self._client.post(
            f"{API_ROOT}/Accounts/{self.account_sid}/Calls.json", data=data
        )
        response.raise_for_status()
        return response.json()["sid"]

    async def update_call(self, external_id: str, data: dict[str, str]) -> None:
        response = await self._client.post(
            f"{API_ROOT}/Accounts/{self.account_sid}/Calls/{external_id}.json", data=data
        )
        response.raise_for_status()

    async def hangup_call(self, external_id: str) -> None:
        await self.update_call(external_id, {"Status": "completed"})

    async def transfer_call(self, external_id: str, destination: str) -> None:
        await self.update_call(
            external_id, {"Twiml": f"<Response><Dial>{destination}</Dial></Response>"}
        )

    def _classify_error(self, status: int, body: str) -> FailureCategory:
        return _category_for_status(status, body)


def _category_for_status(status: int, body: str) -> FailureCategory:
    if status == 429:
        return FailureCategory.RATE_LIMITED
    if status >= 500:
        return FailureCategory.PROVIDER_ERROR
    # 21211/21217 are Twilio's "invalid 'To' number" codes.
    if any(code in body for code in ("21211", "21217", "21214")):
        return FailureCategory.INVALID_NUMBER
    return FailureCategory.AGENT_CONFIG


@register("twilio")
def build_twilio(settings: Settings) -> TwilioTelephonyProvider:
    return TwilioTelephonyProvider(**TwilioSettings().model_dump())
