"""HTTP surface: agent CRUD, call control, simulation, queue ops and analytics."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from app.agents.workflow import DEFAULT_WORKFLOW
from app.worker.call_worker import CallWorker

API = "/api/v1"


@pytest.fixture
def agent_payload() -> dict:
    return {
        "name": "Ava",
        "description": "Support triage",
        "system_prompt": "You are Ava.",
        "workflow": copy.deepcopy(DEFAULT_WORKFLOW),
    }


async def create_agent(client, payload) -> dict:
    response = await client.post(f"{API}/agents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------- health ---------------------------------


async def test_health_and_readiness(client):
    assert (await client.get("/health")).json()["status"] == "ok"
    ready = await client.get("/health/ready")
    body = ready.json()
    assert ready.status_code == 200, body
    assert body["database"] == "ok" and body["redis"] == "ok"
    assert body["providers"]["telephony"] == "mock"


# --------------------------------- agents ---------------------------------


async def test_agent_crud_round_trip(client, agent_payload):
    created = await create_agent(client, agent_payload)
    assert created["version"] == 1
    assert created["is_active"] is True

    listing = (await client.get(f"{API}/agents")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == created["id"]

    patched = await client.patch(
        f"{API}/agents/{created['id']}", json={"description": "Updated", "max_turns": 30}
    )
    assert patched.status_code == 200
    assert patched.json()["description"] == "Updated"
    assert patched.json()["version"] == 1, "only workflow changes bump the version"

    workflow = copy.deepcopy(DEFAULT_WORKFLOW)
    workflow["nodes"][0]["prompt"] = "Hello, Northwind support."
    bumped = await client.patch(f"{API}/agents/{created['id']}", json={"workflow": workflow})
    assert bumped.json()["version"] == 2

    assert (await client.delete(f"{API}/agents/{created['id']}")).status_code == 204
    assert (await client.get(f"{API}/agents/{created['id']}")).status_code == 404


async def test_duplicate_agent_name_is_rejected(client, agent_payload):
    await create_agent(client, agent_payload)
    assert (await client.post(f"{API}/agents", json=agent_payload)).status_code == 409


async def test_invalid_workflow_is_rejected_at_the_api_boundary(client, agent_payload):
    agent_payload["workflow"] = {
        "start_node": "a",
        "nodes": [{"id": "a", "type": "say", "prompt": "hi", "next": "nowhere"}],
    }
    response = await client.post(f"{API}/agents", json=agent_payload)
    assert response.status_code == 422
    assert "undefined nodes" in response.text


async def test_agent_search_and_active_filter(client, agent_payload):
    await create_agent(client, agent_payload)
    second = copy.deepcopy(agent_payload)
    second["name"] = "Billing Bot"
    second["is_active"] = False
    await create_agent(client, second)

    assert (await client.get(f"{API}/agents", params={"search": "bill"})).json()["total"] == 1
    assert (await client.get(f"{API}/agents", params={"is_active": False})).json()["total"] == 1


async def test_simulation_runs_the_workflow_without_placing_a_call(client, agent_payload):
    agent = await create_agent(client, agent_payload)
    response = await client.post(
        f"{API}/agents/{agent['id']}/simulate",
        json={"to_number": "+15551111", "context": {"company": "Northwind"}, "seed": 42},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["turns"], "a simulation should produce a transcript"
    assert body["node_path"][0] == "greeting"
    assert body["outcome"]

    # Same seed, same conversation (latencies are measured, so compare content).
    again = await client.post(
        f"{API}/agents/{agent['id']}/simulate",
        json={"to_number": "+15551111", "context": {"company": "Northwind"}, "seed": 42},
    )
    spoken = lambda payload: [(t["role"], t["text"]) for t in payload["turns"]]  # noqa: E731
    assert spoken(again.json()) == spoken(body)
    assert again.json()["node_path"] == body["node_path"]


# ---------------------------------- calls ----------------------------------


async def test_create_and_fetch_a_call(client, agent_payload, queue):
    agent = await create_agent(client, agent_payload)
    response = await client.post(
        f"{API}/calls",
        json={
            "agent_id": agent["id"],
            "to_number": "+15551111",
            "priority": "high",
            "metadata": {"customer_name": "Dana"},
        },
    )
    assert response.status_code == 201, response.text
    call = response.json()
    assert call["status"] == "queued"
    assert call["call_metadata"] == {"customer_name": "Dana"}

    detail = (await client.get(f"{API}/calls/{call['id']}")).json()
    assert detail["queue_position"] == 0
    assert any(e["type"] == "enqueued" for e in detail["events"])
    assert (await queue.stats()).ready == 1


async def test_idempotency_key_prevents_duplicate_calls(client, agent_payload):
    agent = await create_agent(client, agent_payload)
    body = {"agent_id": agent["id"], "to_number": "+15551111", "idempotency_key": "batch-1:row-7"}
    first = await client.post(f"{API}/calls", json=body)
    second = await client.post(f"{API}/calls", json=body)
    assert first.json()["id"] == second.json()["id"]
    assert (await client.get(f"{API}/calls")).json()["total"] == 1


async def test_call_for_an_unknown_agent_is_404(client):
    response = await client.post(
        f"{API}/calls",
        json={"agent_id": "00000000-0000-0000-0000-000000000000", "to_number": "+15551111"},
    )
    assert response.status_code == 404


async def test_inactive_agent_cannot_receive_calls(client, agent_payload):
    agent_payload["is_active"] = False
    agent = await create_agent(client, agent_payload)
    response = await client.post(
        f"{API}/calls", json={"agent_id": agent["id"], "to_number": "+15551111"}
    )
    assert response.status_code == 422
    assert "not active" in response.text


async def test_bulk_call_creation(client, agent_payload, queue):
    agent = await create_agent(client, agent_payload)
    response = await client.post(
        f"{API}/calls/bulk",
        json={
            "agent_id": agent["id"],
            "priority": "urgent",
            "recipients": [
                {"to_number": "+15551111", "metadata": {"row": 1}},
                {"to_number": "+15552222", "metadata": {"row": 2}},
                {"to_number": "+15553333", "metadata": {"row": 3}},
            ],
        },
    )
    assert response.status_code == 201
    assert len(response.json()) == 3
    assert (await queue.stats()).ready == 3


async def test_scheduled_call_is_parked(client, agent_payload, queue):
    agent = await create_agent(client, agent_payload)
    when = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
    call = (
        await client.post(
            f"{API}/calls",
            json={"agent_id": agent["id"], "to_number": "+15551111", "scheduled_at": when},
        )
    ).json()
    assert call["status"] == "scheduled"
    assert (await queue.stats()).scheduled == 1


async def test_cancel_and_retry_lifecycle(client, agent_payload, queue):
    agent = await create_agent(client, agent_payload)
    call = (
        await client.post(f"{API}/calls", json={"agent_id": agent["id"], "to_number": "+15551111"})
    ).json()

    cancelled = await client.post(f"{API}/calls/{call['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "canceled"
    assert (await queue.stats()).ready == 0

    # Cancelling twice is a conflict, not a crash.
    assert (await client.post(f"{API}/calls/{call['id']}/cancel")).status_code == 409

    retried = await client.post(f"{API}/calls/{call['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "queued"
    assert (await queue.stats()).ready == 1


async def test_call_filters(client, agent_payload):
    agent = await create_agent(client, agent_payload)
    for number, priority in (("+15551111", "urgent"), ("+15552222", "low")):
        await client.post(
            f"{API}/calls",
            json={"agent_id": agent["id"], "to_number": number, "priority": priority},
        )

    assert (await client.get(f"{API}/calls", params={"priority": "urgent"})).json()["total"] == 1
    assert (await client.get(f"{API}/calls", params={"to_number": "2222"})).json()["total"] == 1
    assert (await client.get(f"{API}/calls", params={"status": "queued"})).json()["total"] == 2
    assert (await client.get(f"{API}/calls", params={"failed_only": True})).json()["total"] == 0


# ------------------------------ queue + analytics ------------------------------


async def test_queue_stats_and_dead_letter_requeue(client, agent_payload, queue):
    agent = await create_agent(client, agent_payload)
    call = (
        await client.post(f"{API}/calls", json={"agent_id": agent["id"], "to_number": "+15550000"})
    ).json()

    worker = CallWorker()
    await worker.tick()
    await worker._drain()

    stats = (await client.get(f"{API}/queue/stats")).json()
    assert stats["dead_letter"] == 1

    entries = (await client.get(f"{API}/queue/dead-letter")).json()
    assert entries[0]["call_id"] == call["id"]
    assert "invalid_number" in entries[0]["last_error"]

    requeued = await client.post(f"{API}/queue/dead-letter/{call['id']}/requeue")
    assert requeued.status_code == 200
    assert (await client.get(f"{API}/queue/stats")).json()["dead_letter"] == 0
    assert (await client.get(f"{API}/calls/{call['id']}")).json()["status"] == "queued"


async def test_requeue_of_an_unknown_dead_letter_is_404(client):
    response = await client.post(
        f"{API}/queue/dead-letter/00000000-0000-0000-0000-000000000000/requeue"
    )
    assert response.status_code == 404


async def test_analytics_reflect_completed_calls(client, agent_payload):
    agent = await create_agent(client, agent_payload)
    for _ in range(3):
        await client.post(f"{API}/calls", json={"agent_id": agent["id"], "to_number": "+15551111"})

    worker = CallWorker()
    await worker.tick()
    await worker._drain()

    overview = (await client.get(f"{API}/analytics/overview", params={"days": 1})).json()
    assert overview["total_calls"] == 3
    assert overview["completed"] == 3
    assert overview["outcomes"]
    assert overview["turn_latency"]["samples"] > 0
    assert overview["total_cost_cents"] > 0

    series = (await client.get(f"{API}/analytics/timeseries", params={"days": 2})).json()
    assert sum(point["total"] for point in series) == 3

    per_agent = (await client.get(f"{API}/analytics/agents")).json()
    assert per_agent[0]["agent_name"] == "Ava"
    assert per_agent[0]["total_calls"] == 3
    assert per_agent[0]["avg_turns"] > 0


async def test_agent_stats_endpoint(client, agent_payload):
    agent = await create_agent(client, agent_payload)
    await client.post(f"{API}/calls", json={"agent_id": agent["id"], "to_number": "+15551111"})
    worker = CallWorker()
    await worker.tick()
    await worker._drain()

    stats = (await client.get(f"{API}/agents/{agent['id']}/stats")).json()
    assert stats["total_calls"] == 1
    assert stats["completed"] == 1


async def test_agent_with_queued_calls_cannot_be_deleted(client, agent_payload):
    agent = await create_agent(client, agent_payload)
    await client.post(f"{API}/calls", json={"agent_id": agent["id"], "to_number": "+15551111"})
    response = await client.delete(f"{API}/agents/{agent['id']}")
    assert response.status_code == 409
    assert "deactivate it instead" in response.text
