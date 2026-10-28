"""Provider catalog and spend guard: how a carrier is selected, and kept cheap."""

from __future__ import annotations

import sys
import textwrap

import pytest

from app.core.config import Settings
from app.core.enums import FailureCategory
from app.voice.base import DialRequest, TelephonyError
from app.voice.registry import build_telephony
from app.voice.telephony import providers
from app.voice.telephony.guard import GuardedTelephony
from app.voice.telephony.mock import MockTelephonyProvider


def test_built_in_providers_are_discovered():
    providers.discover()
    assert {"mock", "twilio", "telnyx"} <= set(providers.available())


def test_unknown_provider_lists_what_is_available():
    with pytest.raises(ValueError, match=r"unknown TELEPHONY_PROVIDER: nope.*telnyx"):
        build_telephony(Settings(telephony_provider="nope"))


def test_duplicate_registration_is_rejected():
    with pytest.raises(ValueError, match="already registered"):
        providers.register("mock")(lambda settings: None)  # type: ignore[arg-type,return-value]


def test_mock_is_never_wrapped():
    assert isinstance(build_telephony(Settings(telephony_provider="mock")), MockTelephonyProvider)


def test_a_plugin_module_adds_a_carrier_without_touching_core(tmp_path, monkeypatch):
    (tmp_path / "acme_carrier.py").write_text(
        textwrap.dedent(
            """
            from app.voice.telephony.providers import register

            class Acme:
                name = "acme"
                async def dial(self, request):
                    raise NotImplementedError

            @register("acme")
            def build(settings):
                return Acme()
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(providers._FACTORIES, "_sentinel", lambda s: None)  # restored on exit
    try:
        built = build_telephony(
            Settings(telephony_provider="acme", telephony_plugins="acme_carrier")
        )
        assert built.name == "acme"
        # Real providers are guarded; the allowlist/limit config applies to it too.
        assert isinstance(built, GuardedTelephony)
    finally:
        providers._FACTORIES.pop("acme", None)
        sys.modules.pop("acme_carrier", None)


def test_real_provider_without_credentials_fails_at_startup(monkeypatch):
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TELNYX_API_KEY"):
        build_telephony(Settings(telephony_provider="telnyx"))


def test_real_provider_is_guarded_from_settings(monkeypatch):
    for key, value in {
        "TELNYX_API_KEY": "key",
        "TELNYX_CONNECTION_ID": "conn",
        "TELNYX_FROM_NUMBER": "+15550100",
        "TELNYX_PUBLIC_BASE_URL": "https://voiceops.example.com",
    }.items():
        monkeypatch.setenv(key, value)
    built = build_telephony(
        Settings(
            telephony_provider="telnyx",
            telephony_allowed_numbers="+1 (555) 010-0199",
            telephony_max_call_seconds=120,
        )
    )
    assert isinstance(built, GuardedTelephony)
    assert built.name == "telnyx"


class _Recorder:
    name = "recorder"

    def __init__(self) -> None:
        self.requests: list[DialRequest] = []

    async def dial(self, request: DialRequest):
        self.requests.append(request)
        return object()


async def test_guard_refuses_numbers_outside_the_allowlist():
    inner = _Recorder()
    guard = GuardedTelephony(inner, allowed_numbers=frozenset({"+15550100199"}))

    with pytest.raises(TelephonyError) as exc:
        await guard.dial(DialRequest(call_id="c1", to_number="+15550100000"))

    # Permanent, so the retry policy dead-letters it instead of trying again.
    assert exc.value.category is FailureCategory.INVALID_NUMBER
    assert inner.requests == []


async def test_guard_matches_numbers_regardless_of_formatting():
    inner = _Recorder()
    guard = GuardedTelephony(inner, allowed_numbers=frozenset({"+1 (555) 010-0199"}))

    await guard.dial(DialRequest(call_id="c1", to_number="+15550100199"))

    assert len(inner.requests) == 1


async def test_guard_applies_the_call_time_limit_unless_one_is_set():
    inner = _Recorder()
    guard = GuardedTelephony(inner, max_call_seconds=90)

    await guard.dial(DialRequest(call_id="c1", to_number="+15550100199"))
    await guard.dial(DialRequest(call_id="c2", to_number="+15550100199", max_duration_seconds=30))

    assert [r.max_duration_seconds for r in inner.requests] == [90, 30]


async def test_empty_guard_changes_nothing():
    inner = _Recorder()
    guard = GuardedTelephony(inner)

    await guard.dial(DialRequest(call_id="c1", to_number="+15550100199"))

    assert inner.requests[0].max_duration_seconds is None
