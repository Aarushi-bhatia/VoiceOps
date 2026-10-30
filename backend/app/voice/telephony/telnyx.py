"""Telnyx telephony via the Call Control API and bidirectional media streaming.

``dial`` posts to ``/v2/calls`` with a ``stream_url`` pointing back at this
service's ``/ws/media/{call_id}``; Telnyx starts streaming when the callee
answers. Audio is PCMU at 8 kHz, the same as the other carriers here.

Setup in the Telnyx portal: a number, and a Voice API (Call Control) application
whose id is ``TELNYX_CONNECTION_ID``. VoiceOps drives the call over REST and the
media stream, so the application's webhook is not used for call handling.
``TELNYX_PUBLIC_BASE_URL`` must be a public https origin (a tunnel in dev).

A trial account can only dial its one verified number, which makes it a safe,
near-free way to prove the integration; see ``TELEPHONY_ALLOWED_NUMBERS``.
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
    MediaStreamProvider,
    MediaStreamRegistry,
    MediaStreamSession,
)

API_ROOT = "https://api.telnyx.com/v2"


class TelnyxSettings(ProviderSettings):
    model_config = SettingsConfigDict(env_prefix="TELNYX_", **ProviderSettings.base_config)

    api_key: str = ""
    # The Voice API (Call Control) application the call is placed through.
    connection_id: str = ""
    from_number: str = ""
    public_base_url: str = ""


class TelnyxCallSession(MediaStreamSession):
    provider_name = "telnyx"
    # Telnyx accepts RTP chunks from 20 ms up to 30 s, so send one second at a
    # time rather than fifty messages a second.
    outbound_chunk_bytes = 8000

    def _stream_id(self, event: dict[str, Any]) -> str | None:
        return event.get("stream_id")

    def _media_message(self, payload: str) -> dict[str, Any]:
        return {"event": "media", "media": {"payload": payload}}

    def _mark_message(self, name: str) -> dict[str, Any]:
        return {"event": "mark", "mark": {"name": name}}


class TelnyxTelephonyProvider(MediaStreamProvider):
    name = "telnyx"
    session_class = TelnyxCallSession

    def __init__(
        self,
        api_key: str,
        connection_id: str,
        from_number: str,
        public_base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        stream_registry: MediaStreamRegistry | None = None,
    ) -> None:
        self.require(
            "telnyx",
            TELNYX_API_KEY=api_key,
            TELNYX_CONNECTION_ID=connection_id,
            TELNYX_FROM_NUMBER=from_number,
            TELNYX_PUBLIC_BASE_URL=public_base_url,
        )
        super().__init__(
            public_base_url,
            client
            or httpx.AsyncClient(
                base_url=API_ROOT,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=httpx.Timeout(20.0),
            ),
            stream_registry,
        )
        self.connection_id = connection_id
        self.from_number = from_number

    async def _place_call(self, request: DialRequest, stream_url: str) -> str:
        body: dict[str, Any] = {
            "connection_id": self.connection_id,
            "to": request.to_number,
            "from": request.from_number or self.from_number,
            "timeout_secs": int(request.timeout_seconds),
            "stream_url": stream_url,
            "stream_track": "inbound_track",
            "stream_bidirectional_mode": "rtp",
            "stream_bidirectional_codec": "PCMU",
        }
        if request.max_duration_seconds:
            body["time_limit_secs"] = request.max_duration_seconds
        response = await self._client.post("/calls", json=body)
        response.raise_for_status()
        return response.json()["data"]["call_control_id"]

    async def _action(self, external_id: str, action: str, body: dict[str, Any]) -> None:
        response = await self._client.post(f"/calls/{external_id}/actions/{action}", json=body)
        response.raise_for_status()

    async def hangup_call(self, external_id: str) -> None:
        await self._action(external_id, "hangup", {})

    async def transfer_call(self, external_id: str, destination: str) -> None:
        await self._action(external_id, "transfer", {"to": destination})

    def _classify_error(self, status: int, body: str) -> FailureCategory:
        if status == 429:
            return FailureCategory.RATE_LIMITED
        if status >= 500:
            return FailureCategory.PROVIDER_ERROR
        # Telnyx reports a bad or disallowed destination as a 4xx whose message
        # names the destination; anything else is our configuration.
        if status in (400, 422) and "destination" in body.lower():
            return FailureCategory.INVALID_NUMBER
        return FailureCategory.AGENT_CONFIG


@register("telnyx")
def build_telnyx(settings: Settings) -> TelnyxTelephonyProvider:
    return TelnyxTelephonyProvider(**TelnyxSettings().model_dump())
