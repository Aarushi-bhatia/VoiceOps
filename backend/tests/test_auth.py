"""API-key roles.

Transcripts contain personal data and agent workflows script what customers
hear, so those are not the same permission. With no keys configured
authentication is off, which keeps local development simple - and `/health`
reports that, so an unprotected deployment is visible rather than silent.
"""

from __future__ import annotations

import pytest

from app.core.security import Role, parse_api_keys, resolve_role

API = "/api/v1"
VIEWER, OPERATOR, ADMIN = "vkey", "okey", "akey"
KEYS = f"{VIEWER}:viewer,{OPERATOR}:operator,{ADMIN}:admin"


@pytest.fixture
def secured(settings):
    settings.api_keys = KEYS
    return settings


def head(key: str | None) -> dict:
    return {"X-API-Key": key} if key else {}


# ------------------------------ parsing ------------------------------


def test_keys_parse_with_roles_and_ignore_rubbish():
    parsed = parse_api_keys("a:admin, b:viewer , c:not-a-role, d, ,")
    assert parsed == {"a": Role.ADMIN, "b": Role.VIEWER, "d": Role.VIEWER}


def test_roles_are_ordered():
    assert Role.ADMIN.can(Role.VIEWER) and Role.ADMIN.can(Role.OPERATOR)
    assert Role.OPERATOR.can(Role.VIEWER)
    assert not Role.VIEWER.can(Role.OPERATOR)
    assert not Role.OPERATOR.can(Role.ADMIN)


def test_no_configured_keys_means_authentication_is_disabled(settings):
    settings.api_keys = None
    assert resolve_role(None, settings) is Role.ADMIN


def test_an_unknown_key_resolves_to_nothing(secured):
    assert resolve_role("wrong", secured) is None
    assert resolve_role(None, secured) is None


# ------------------------------ the API ------------------------------


async def test_health_is_public_and_reports_whether_auth_is_on(client, secured):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["auth"] == "enabled"


async def test_health_flags_an_unprotected_deployment(client, settings):
    settings.api_keys = None
    assert (await client.get("/health")).json()["auth"] == "disabled"


async def test_no_key_is_rejected(client, secured):
    response = await client.get(f"{API}/calls")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_a_bad_key_is_rejected(client, secured):
    assert (await client.get(f"{API}/calls", headers=head("nope"))).status_code == 401


async def test_a_bearer_token_works_too(client, secured):
    response = await client.get(f"{API}/calls", headers={"Authorization": f"Bearer {VIEWER}"})
    assert response.status_code == 200


async def test_a_viewer_can_read_but_not_act(client, secured, agent_payload):
    assert (await client.get(f"{API}/calls", headers=head(VIEWER))).status_code == 200
    assert (await client.get(f"{API}/queue/stats", headers=head(VIEWER))).status_code == 200
    assert (await client.get(f"{API}/analytics/overview", headers=head(VIEWER))).status_code == 200

    blocked = await client.post(f"{API}/agents", json=agent_payload, headers=head(VIEWER))
    assert blocked.status_code == 403
    assert "admin" in blocked.json()["detail"]


async def test_an_operator_can_queue_calls_but_not_rewrite_an_agent(client, secured, agent_payload):
    agent = (await client.post(f"{API}/agents", json=agent_payload, headers=head(ADMIN))).json()

    queued = await client.post(
        f"{API}/calls",
        json={"agent_id": agent["id"], "to_number": "+15551110001111"},
        headers=head(OPERATOR),
    )
    assert queued.status_code == 201

    assert (
        await client.patch(
            f"{API}/agents/{agent['id']}", json={"description": "nope"}, headers=head(OPERATOR)
        )
    ).status_code == 403


async def test_only_an_admin_can_change_the_do_not_call_list(client, secured):
    body = {"number": "+15557778888", "reason": "opted out"}
    assert (
        await client.post(f"{API}/suppression", json=body, headers=head(OPERATOR))
    ).status_code == 403
    assert (
        await client.post(f"{API}/suppression", json=body, headers=head(ADMIN))
    ).status_code == 201
    assert (await client.get(f"{API}/suppression", headers=head(VIEWER))).status_code == 200


async def test_only_an_admin_can_purge_the_dead_letter_queue(client, secured):
    path = f"{API}/queue/dead-letter/00000000-0000-0000-0000-000000000000"
    assert (await client.delete(path, headers=head(OPERATOR))).status_code == 403
    # Admin gets past the guard and hits the real 404 for an unknown call.
    assert (await client.delete(path, headers=head(ADMIN))).status_code == 404


async def test_everything_is_open_when_no_keys_are_configured(client, settings, agent_payload):
    settings.api_keys = None
    assert (await client.post(f"{API}/agents", json=agent_payload)).status_code == 201
