"""Redis access layer.

Two backends implement the same small command surface:

* :class:`RealRedis`  - `redis.asyncio`, used whenever ``REDIS_URL`` is set.
* :class:`InMemoryRedis` - a single-process stub so the platform runs on a
  machine without Redis. It is *not* a general Redis emulator; it implements
  exactly the commands the queue uses.

Atomic multi-step operations are expressed as :class:`Script` objects that
carry a Lua body (executed server-side by the real backend) alongside a Python
transliteration used by the stub. Both live side by side in
``app.queue.scripts`` so they are reviewed together.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Script:
    """An atomic operation with a Lua body and an equivalent Python fallback."""

    name: str
    lua: str
    fallback: Callable[[InMemoryRedis, list[str], list[str]], Any]


class RedisBackend(Protocol):
    async def ping(self) -> bool: ...
    async def zadd(self, key: str, mapping: dict[str, float], *, xx: bool = False) -> int: ...
    async def zrem(self, key: str, *members: str) -> int: ...
    async def zcard(self, key: str) -> int: ...
    async def zscore(self, key: str, member: str) -> float | None: ...
    async def zrange(
        self, key: str, start: int, end: int, *, withscores: bool = False
    ) -> list[Any]: ...
    async def zrangebyscore(
        self, key: str, min_: float, max_: float, *, offset: int = 0, count: int | None = None
    ) -> list[str]: ...
    async def hset(self, key: str, field: str, value: str) -> int: ...
    async def hget(self, key: str, field: str) -> str | None: ...
    async def hgetall(self, key: str) -> dict[str, str]: ...
    async def hdel(self, key: str, *fields: str) -> int: ...
    async def hlen(self, key: str) -> int: ...
    async def incrby(self, key: str, amount: int = 1) -> int: ...
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str) -> bool: ...
    async def delete(self, *keys: str) -> int: ...
    async def keys(self, pattern: str) -> list[str]: ...
    async def publish(self, channel: str, message: str) -> int: ...
    async def run_script(self, script: Script, keys: list[str], args: list[Any]) -> Any: ...
    def listen(self, channel: str) -> AsyncIterator[str]: ...
    async def close(self) -> None: ...


class RealRedis:
    """Thin adapter over `redis.asyncio.Redis` (decoded responses)."""

    def __init__(self, url: str) -> None:
        from redis.asyncio import Redis

        self._client = Redis.from_url(
            url, decode_responses=True, health_check_interval=30, socket_keepalive=True
        )
        self._scripts: dict[str, Any] = {}

    @property
    def raw(self) -> Any:
        return self._client

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def zadd(self, key: str, mapping: dict[str, float], *, xx: bool = False) -> int:
        return int(await self._client.zadd(key, mapping, xx=xx))

    async def zrem(self, key: str, *members: str) -> int:
        if not members:
            return 0
        return int(await self._client.zrem(key, *members))

    async def zcard(self, key: str) -> int:
        return int(await self._client.zcard(key))

    async def zscore(self, key: str, member: str) -> float | None:
        score = await self._client.zscore(key, member)
        return None if score is None else float(score)

    async def zrange(
        self, key: str, start: int, end: int, *, withscores: bool = False
    ) -> list[Any]:
        return list(await self._client.zrange(key, start, end, withscores=withscores))

    async def zrangebyscore(
        self, key: str, min_: float, max_: float, *, offset: int = 0, count: int | None = None
    ) -> list[str]:
        kwargs: dict[str, Any] = {}
        if count is not None:
            kwargs = {"start": offset, "num": count}
        return list(await self._client.zrangebyscore(key, min_, max_, **kwargs))

    async def hset(self, key: str, field: str, value: str) -> int:
        return int(await self._client.hset(key, field, value))

    async def hget(self, key: str, field: str) -> str | None:
        return await self._client.hget(key, field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(await self._client.hgetall(key))

    async def hdel(self, key: str, *fields: str) -> int:
        if not fields:
            return 0
        return int(await self._client.hdel(key, *fields))

    async def hlen(self, key: str) -> int:
        return int(await self._client.hlen(key))

    async def incrby(self, key: str, amount: int = 1) -> int:
        return int(await self._client.incrby(key, amount))

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str) -> bool:
        return bool(await self._client.set(key, value))

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return int(await self._client.delete(*keys))

    async def keys(self, pattern: str) -> list[str]:
        return list(await self._client.keys(pattern))

    async def publish(self, channel: str, message: str) -> int:
        return int(await self._client.publish(channel, message))

    async def run_script(self, script: Script, keys: list[str], args: list[Any]) -> Any:
        registered = self._scripts.get(script.name)
        if registered is None:
            registered = self._client.register_script(script.lua)
            self._scripts[script.name] = registered
        return await registered(keys=keys, args=[str(a) for a in args])

    async def listen(self, channel: str) -> AsyncIterator[str]:
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message and message.get("type") == "message":
                    yield message["data"]
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def close(self) -> None:
        await self._client.aclose()


class InMemoryRedis:
    """Single-process stand-in for Redis covering the queue's command surface.

    Every call runs under one asyncio lock, which gives scripts the same
    all-or-nothing execution that Redis provides.
    """

    def __init__(self) -> None:
        self._z: dict[str, dict[str, float]] = defaultdict(dict)
        self._h: dict[str, dict[str, str]] = defaultdict(dict)
        self._kv: dict[str, str] = {}
        self._channels: dict[str, list[asyncio.Queue[str]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    # -- introspection used by the script fallbacks (already under the lock) --

    def _sorted(self, key: str) -> list[tuple[str, float]]:
        # Redis orders by score, then lexicographically by member.
        return sorted(self._z.get(key, {}).items(), key=lambda kv: (kv[1], kv[0]))

    def s_zadd(self, key: str, member: str, score: float) -> None:
        self._z[key][member] = score

    def s_zrem(self, key: str, member: str) -> int:
        return 1 if self._z.get(key, {}).pop(member, None) is not None else 0

    def s_zscore(self, key: str, member: str) -> float | None:
        return self._z.get(key, {}).get(member)

    def s_zrange_by_score(
        self, key: str, min_: float, max_: float, count: int | None = None
    ) -> list[str]:
        out = [m for m, s in self._sorted(key) if min_ <= s <= max_]
        return out[:count] if count is not None else out

    def s_ztop(self, key: str, count: int) -> list[str]:
        return [m for m, _ in self._sorted(key)[:count]]

    def s_hget(self, key: str, field: str) -> str | None:
        return self._h.get(key, {}).get(field)

    def s_hset(self, key: str, field: str, value: str) -> None:
        self._h[key][field] = value

    def s_hdel(self, key: str, field: str) -> int:
        return 1 if self._h.get(key, {}).pop(field, None) is not None else 0

    # ------------------------------ commands ------------------------------

    async def ping(self) -> bool:
        return True

    async def zadd(self, key: str, mapping: dict[str, float], *, xx: bool = False) -> int:
        async with self._lock:
            added = 0
            for member, score in mapping.items():
                exists = member in self._z[key]
                if xx and not exists:
                    continue
                if not exists:
                    added += 1
                self._z[key][member] = float(score)
            return added

    async def zrem(self, key: str, *members: str) -> int:
        async with self._lock:
            return sum(self.s_zrem(key, m) for m in members)

    async def zcard(self, key: str) -> int:
        async with self._lock:
            return len(self._z.get(key, {}))

    async def zscore(self, key: str, member: str) -> float | None:
        async with self._lock:
            return self.s_zscore(key, member)

    async def zrange(
        self, key: str, start: int, end: int, *, withscores: bool = False
    ) -> list[Any]:
        async with self._lock:
            items = self._sorted(key)
            stop = None if end == -1 else end + 1
            window = items[start:stop]
            return [(m, s) for m, s in window] if withscores else [m for m, _ in window]

    async def zrangebyscore(
        self, key: str, min_: float, max_: float, *, offset: int = 0, count: int | None = None
    ) -> list[str]:
        async with self._lock:
            out = self.s_zrange_by_score(key, min_, max_)
            out = out[offset:]
            return out[:count] if count is not None else out

    async def hset(self, key: str, field: str, value: str) -> int:
        async with self._lock:
            new = field not in self._h[key]
            self._h[key][field] = value
            return int(new)

    async def hget(self, key: str, field: str) -> str | None:
        async with self._lock:
            return self.s_hget(key, field)

    async def hgetall(self, key: str) -> dict[str, str]:
        async with self._lock:
            return dict(self._h.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        async with self._lock:
            return sum(self.s_hdel(key, f) for f in fields)

    async def hlen(self, key: str) -> int:
        async with self._lock:
            return len(self._h.get(key, {}))

    async def incrby(self, key: str, amount: int = 1) -> int:
        async with self._lock:
            value = int(self._kv.get(key, "0")) + amount
            self._kv[key] = str(value)
            return value

    async def get(self, key: str) -> str | None:
        async with self._lock:
            return self._kv.get(key)

    async def set(self, key: str, value: str) -> bool:
        async with self._lock:
            self._kv[key] = value
            return True

    async def delete(self, *keys: str) -> int:
        async with self._lock:
            removed = 0
            for key in keys:
                removed += int(self._z.pop(key, None) is not None)
                removed += int(self._h.pop(key, None) is not None)
                removed += int(self._kv.pop(key, None) is not None)
            return removed

    async def keys(self, pattern: str) -> list[str]:
        async with self._lock:
            names = set(self._z) | set(self._h) | set(self._kv)
            return [n for n in names if fnmatch.fnmatch(n, pattern)]

    async def publish(self, channel: str, message: str) -> int:
        async with self._lock:
            subscribers = list(self._channels.get(channel, []))
        for queue in subscribers:
            queue.put_nowait(message)
        return len(subscribers)

    def subscribe(self, channel: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._channels[channel].append(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue[str]) -> None:
        if queue in self._channels.get(channel, []):
            self._channels[channel].remove(queue)

    async def listen(self, channel: str) -> AsyncIterator[str]:
        queue = self.subscribe(channel)
        try:
            while True:
                yield await queue.get()
        finally:
            self.unsubscribe(channel, queue)

    async def run_script(self, script: Script, keys: list[str], args: list[Any]) -> Any:
        async with self._lock:
            return script.fallback(self, keys, [str(a) for a in args])

    async def close(self) -> None:
        self._z.clear()
        self._h.clear()
        self._kv.clear()
        self._channels.clear()


_backend: RedisBackend | None = None


def get_redis() -> RedisBackend:
    """Process-wide Redis backend, chosen from settings on first use."""
    global _backend
    if _backend is None:
        from app.core.config import get_settings

        settings = get_settings()
        if settings.redis_url:
            _backend = RealRedis(settings.redis_url)
            logger.info("using redis backend", extra={"backend": "redis"})
        else:
            _backend = InMemoryRedis()
            logger.warning(
                "REDIS_URL is unset - using the in-process queue stub. "
                "API and worker must run in the same process.",
                extra={"backend": "in-memory"},
            )
    return _backend


def set_redis(backend: RedisBackend | None) -> None:
    """Override the process-wide backend (tests, embedded worker)."""
    global _backend
    _backend = backend


async def close_redis() -> None:
    global _backend
    if _backend is not None:
        await _backend.close()
    _backend = None


def now_ms() -> int:
    return int(time.time() * 1000)
