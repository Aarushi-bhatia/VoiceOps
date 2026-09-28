"""Shared fixtures: an isolated SQLite database, an in-process queue, mock voice."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.workflow import DEFAULT_WORKFLOW
from app.core.config import Settings, get_settings
from app.db.models import Agent
from app.db.session import get_sessionmaker, init_models, reset_engine_for_tests
from app.queue.call_queue import CallQueue, set_queue
from app.queue.redis_client import InMemoryRedis, set_redis
from app.voice.base import VoiceStack
from app.voice.llm.mock import MockLanguageModel
from app.voice.registry import set_voice_stack
from app.voice.stt.mock import MockSpeechToText
from app.voice.telephony.mock import MockTelephonyProvider
from app.voice.tts.mock import MockTextToSpeech


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    # Point VOICEOPS_TEST_DATABASE_URL at a Postgres server to run the same
    # suite against it; each test still gets its own schema-fresh database via
    # a unique name. Unset, the suite uses a throwaway SQLite file and needs
    # no services.
    postgres = os.getenv("VOICEOPS_TEST_DATABASE_URL")
    if postgres:
        monkeypatch.setenv("DATABASE_URL", postgres)
    else:
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    # Empty, not deleted: an explicit env var outranks a developer's .env
    # file, so the suite behaves the same on every machine.
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("QUEUE_NAMESPACE", "test")
    monkeypatch.setenv("MOCK_LATENCY_SCALE", "0")
    monkeypatch.setenv("WORKER_POLL_INTERVAL_MS", "10")
    monkeypatch.setenv("VOICEOPS_ENV", "test")
    get_settings.cache_clear()
    reset_engine_for_tests()
    yield get_settings()
    get_settings.cache_clear()
    reset_engine_for_tests()


async def _clear_namespace(backend, namespace: str) -> None:
    """Drop only this suite's keys - never FLUSHDB someone's Redis."""
    keys = await backend.keys(f"{namespace}:*")
    if keys:
        await backend.delete(*keys)


@pytest.fixture
async def redis(settings: Settings):
    """The queue backend under test.

    Defaults to the in-process stub so the suite needs no services. Set
    ``VOICEOPS_TEST_REDIS_URL`` to run the very same tests against a real
    Redis, which is the only way the Lua scripts in app.queue.scripts get
    executed - the stub runs their Python transliterations instead.

        VOICEOPS_TEST_REDIS_URL=redis://localhost:6379/15 pytest
    """
    url = os.getenv("VOICEOPS_TEST_REDIS_URL")
    if not url:
        backend = InMemoryRedis()
        set_redis(backend)
        yield backend
        set_redis(None)
        return

    from app.queue.redis_client import RealRedis

    backend = RealRedis(url)
    await _clear_namespace(backend, settings.queue_namespace)
    set_redis(backend)
    try:
        yield backend
    finally:
        await _clear_namespace(backend, settings.queue_namespace)
        set_redis(None)
        await backend.close()


@pytest.fixture
async def queue(settings: Settings, redis) -> CallQueue:
    q = CallQueue(redis=redis, settings=settings)
    set_queue(q)
    yield q
    set_queue(None)


@pytest.fixture
def stack(settings: Settings) -> VoiceStack:
    """Mock providers with latency disabled so tests run at full speed."""
    voice = VoiceStack(
        stt=MockSpeechToText(latency_scale=0.0),
        tts=MockTextToSpeech(latency_scale=0.0),
        llm=MockLanguageModel(latency_scale=0.0),
        telephony=MockTelephonyProvider(seed=settings.mock_seed, latency_scale=0.0),
    )
    set_voice_stack(voice)
    yield voice
    set_voice_stack(None)


@pytest.fixture
async def clean_database(settings: Settings):
    """Give every test an empty schema.

    SQLite gets this for free - each test has its own file. A shared Postgres
    does not, so the tables are truncated first. Anything that touches the
    database depends on this, including the API client.
    """
    await init_models()
    if os.getenv("VOICEOPS_TEST_DATABASE_URL"):
        await _truncate_all()
    yield


@pytest.fixture
async def db(clean_database):
    async with get_sessionmaker()() as session:
        yield session


async def _truncate_all() -> None:
    from sqlalchemy import text

    from app.db.base import Base
    from app.db.session import get_engine

    names = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def agent(db) -> Agent:
    row = Agent(
        name="Ava",
        description="Support triage agent",
        workflow=DEFAULT_WORKFLOW,
        system_prompt="You are Ava, a support agent for Northwind.",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest.fixture
async def client(
    settings: Settings, queue: CallQueue, stack: VoiceStack, clean_database
) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    app = create_app(settings)
    # Tests drive the worker explicitly so assertions are deterministic.
    app.state.embedded_worker = False
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http_client:
            yield http_client


@pytest.fixture
def agent_payload() -> dict:
    """Body for creating an agent over the API."""
    import copy

    return {
        "name": "Ava",
        "description": "Support triage",
        "system_prompt": "You are Ava.",
        "workflow": copy.deepcopy(DEFAULT_WORKFLOW),
    }
