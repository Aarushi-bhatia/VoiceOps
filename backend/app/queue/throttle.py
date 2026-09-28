"""Carrier rate limiting and the do-not-call list.

The platform can place calls far faster than a carrier will accept them - a
number typically allows about one call per second - so the constraint is not
worker capacity, it is the line. Workers take a token before dialling and defer
the call if there isn't one.

Both live in Redis beside the queue, so every worker shares one view.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime  # noqa: TC003 - used in annotations

from app.queue.redis_client import RedisBackend, Script, now_ms

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# token bucket: refills at `rate` per second up to `burst`, one token per call.
# KEYS: bucket_hash     ARGV: rate, burst, now_ms
# --------------------------------------------------------------------------
_TAKE_TOKEN_LUA = """
local key = KEYS[1]
local rate, burst, now = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
local tokens = tonumber(redis.call('HGET', key, 'tokens'))
local updated = tonumber(redis.call('HGET', key, 'at'))
if tokens == nil or updated == nil then
  tokens = burst
  updated = now
end
tokens = math.min(burst, tokens + ((now - updated) / 1000) * rate)
if tokens < 1 then
  redis.call('HSET', key, 'tokens', tokens, 'at', now)
  redis.call('EXPIRE', key, 3600)
  -- milliseconds until one whole token exists again
  return math.ceil(((1 - tokens) / rate) * 1000)
end
redis.call('HSET', key, 'tokens', tokens - 1, 'at', now)
redis.call('EXPIRE', key, 3600)
return 0
"""


def _take_token(r, keys: list[str], args: list[str]) -> int:
    key = keys[0]
    rate, burst, now = float(args[0]), float(args[1]), float(args[2])
    raw_tokens = r.s_hget(key, "tokens")
    raw_at = r.s_hget(key, "at")
    if raw_tokens is None or raw_at is None:
        tokens, updated = burst, now
    else:
        tokens, updated = float(raw_tokens), float(raw_at)

    tokens = min(burst, tokens + ((now - updated) / 1000) * rate)
    if tokens < 1:
        r.s_hset(key, "tokens", str(tokens))
        r.s_hset(key, "at", str(now))
        import math

        return math.ceil(((1 - tokens) / rate) * 1000)
    r.s_hset(key, "tokens", str(tokens - 1))
    r.s_hset(key, "at", str(now))
    return 0


TAKE_TOKEN = Script("voiceops_take_token", _TAKE_TOKEN_LUA, _take_token)


def normalise_number(number: str) -> str:
    """Compare numbers by digits only, so +1 555-111 and +1555111 match."""
    digits = re.sub(r"\D", "", number or "")
    return digits[-15:]


class CarrierThrottle:
    """Shared, per-outbound-number rate limit."""

    def __init__(
        self,
        redis: RedisBackend,
        namespace: str,
        *,
        calls_per_second: float = 1.0,
        burst: int = 5,
    ) -> None:
        self.redis = redis
        self.prefix = f"{namespace}:throttle"
        self.calls_per_second = max(calls_per_second, 0.0)
        self.burst = max(burst, 1)

    async def acquire(self, from_number: str | None) -> float:
        """Take a token. Returns 0 if allowed, else seconds to wait."""
        if self.calls_per_second <= 0:
            return 0.0
        line = normalise_number(from_number or "") or "default"
        wait_ms = await self.redis.run_script(
            TAKE_TOKEN,
            [f"{self.prefix}:{line}"],
            [self.calls_per_second, self.burst, now_ms()],
        )
        return max(int(wait_ms or 0), 0) / 1000


class DoNotCallList:
    """Numbers that must never be dialled again."""

    def __init__(self, redis: RedisBackend, namespace: str) -> None:
        self.redis = redis
        self.key = f"{namespace}:dnc"

    async def add(self, number: str, reason: str = "") -> None:
        await self.redis.hset(self.key, normalise_number(number), reason or "no reason given")
        logger.info("number added to do-not-call", extra={"number": number})

    async def remove(self, number: str) -> bool:
        return bool(await self.redis.hdel(self.key, normalise_number(number)))

    async def contains(self, number: str) -> bool:
        return await self.redis.hget(self.key, normalise_number(number)) is not None

    async def all(self) -> dict[str, str]:
        return await self.redis.hgetall(self.key)


def next_calling_window(
    now: datetime,
    *,
    start_hour: int,
    end_hour: int,
    timezone_name: str = "UTC",
) -> datetime | None:
    """``None`` if it is fine to call now, otherwise when it next will be.

    Calling someone at 3am is unacceptable and in most countries unlawful, so a
    call outside the window is deferred rather than failed - it keeps its
    attempts and goes out when the window opens.
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    if start_hour == end_hour:
        return None  # disabled

    try:
        local = now.astimezone(ZoneInfo(timezone_name))
    except Exception:  # noqa: BLE001 - an unknown zone must not block calling
        logger.warning("unknown calling-hours timezone", extra={"timezone": timezone_name})
        return None

    def at_hour(day: datetime, hour: int) -> datetime:
        return day.replace(hour=hour, minute=0, second=0, microsecond=0)

    if start_hour < end_hour:
        # A normal daytime window, e.g. 09:00-20:00.
        if start_hour <= local.hour < end_hour:
            return None
        target = at_hour(local, start_hour)
        if local.hour >= end_hour:
            target += timedelta(days=1)
    else:
        # A window spanning midnight, e.g. 20:00-09:00.
        if local.hour >= start_hour or local.hour < end_hour:
            return None
        target = at_hour(local, start_hour)

    return target.astimezone(now.tzinfo)
