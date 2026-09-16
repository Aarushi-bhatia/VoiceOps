"""Atomic queue operations.

Each operation is a :class:`Script` pairing a Lua body (run server-side on real
Redis, so the whole operation is atomic across workers) with a Python
transliteration used by the in-process stub. The two are defined next to each
other deliberately: change one, change the other.
"""

from __future__ import annotations

from typing import Any

from app.queue.redis_client import InMemoryRedis, Script

# Ready-queue score = priority_rank * PRIORITY_STRIDE + enqueued_at_ms.
# The stride dwarfs any realistic millisecond epoch, so a call is ordered first
# by priority band and only then FIFO within the band.
PRIORITY_STRIDE = 1e13


# --------------------------------------------------------------------------
# enqueue: place a call in the ready queue, or park it until scheduled_at.
#
# Also drops any lease the call is holding. A retry is enqueued by the very
# worker that just failed it and still holds its lease; leaving that lease in
# place would let the reaper reclaim the call once it expired, so the call
# would be attempted twice - a second phone call to the same customer. For a
# brand-new call the ZREM is a no-op.
#
# KEYS: ready, scheduled, jobs, meta, processing
# ARGV: call_id, payload_json, rank, due_ms, now_ms
# --------------------------------------------------------------------------
_ENQUEUE_LUA = """
local ready, scheduled, jobs, meta, processing = KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5]
local id, payload = ARGV[1], ARGV[2]
local rank, due, now = tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5])
redis.call('HSET', jobs, id, payload)
redis.call('HSET', meta, id, rank)
redis.call('ZREM', processing, id)
if due > now then
  redis.call('ZREM', ready, id)
  redis.call('ZADD', scheduled, due, id)
  return 'scheduled'
end
redis.call('ZREM', scheduled, id)
redis.call('ZADD', ready, rank * 1e13 + now, id)
return 'ready'
"""


def _enqueue(r: InMemoryRedis, keys: list[str], args: list[str]) -> str:
    ready, scheduled, jobs, meta, processing = keys
    call_id, payload = args[0], args[1]
    rank, due, now = float(args[2]), float(args[3]), float(args[4])
    r.s_hset(jobs, call_id, payload)
    r.s_hset(meta, call_id, str(int(rank)))
    r.s_zrem(processing, call_id)
    if due > now:
        r.s_zrem(ready, call_id)
        r.s_zadd(scheduled, call_id, due)
        return "scheduled"
    r.s_zrem(scheduled, call_id)
    r.s_zadd(ready, call_id, rank * PRIORITY_STRIDE + now)
    return "ready"


ENQUEUE = Script("voiceops_enqueue", _ENQUEUE_LUA, _enqueue)


# --------------------------------------------------------------------------
# promote: move calls whose scheduled time has arrived into the ready queue.
# KEYS: scheduled, ready, meta      ARGV: now_ms, limit
# --------------------------------------------------------------------------
_PROMOTE_LUA = """
local scheduled, ready, meta = KEYS[1], KEYS[2], KEYS[3]
local now, limit = tonumber(ARGV[1]), tonumber(ARGV[2])
local due = redis.call('ZRANGEBYSCORE', scheduled, '-inf', now, 'LIMIT', 0, limit)
for i = 1, #due do
  local id = due[i]
  local rank = tonumber(redis.call('HGET', meta, id)) or 2
  redis.call('ZREM', scheduled, id)
  redis.call('ZADD', ready, rank * 1e13 + now, id)
end
return due
"""


def _promote(r: InMemoryRedis, keys: list[str], args: list[str]) -> list[str]:
    scheduled, ready, meta = keys
    now, limit = float(args[0]), int(args[1])
    due = r.s_zrange_by_score(scheduled, float("-inf"), now, count=limit)
    for call_id in due:
        raw = r.s_hget(meta, call_id)
        rank = float(raw) if raw is not None else 2.0
        r.s_zrem(scheduled, call_id)
        r.s_zadd(ready, call_id, rank * PRIORITY_STRIDE + now)
    return due


PROMOTE = Script("voiceops_promote", _PROMOTE_LUA, _promote)


# --------------------------------------------------------------------------
# claim: atomically take up to `count` calls and hold a lease on each.
# KEYS: ready, processing, jobs     ARGV: count, lease_expiry_ms
# --------------------------------------------------------------------------
_CLAIM_LUA = """
local ready, processing, jobs = KEYS[1], KEYS[2], KEYS[3]
local count, expiry = tonumber(ARGV[1]), tonumber(ARGV[2])
local ids = redis.call('ZRANGE', ready, 0, count - 1)
local out = {}
for i = 1, #ids do
  local id = ids[i]
  redis.call('ZREM', ready, id)
  redis.call('ZADD', processing, expiry, id)
  local payload = redis.call('HGET', jobs, id)
  out[#out + 1] = payload or ''
end
return out
"""


def _claim(r: InMemoryRedis, keys: list[str], args: list[str]) -> list[str]:
    ready, processing, jobs = keys
    count, expiry = int(args[0]), float(args[1])
    out: list[str] = []
    for call_id in r.s_ztop(ready, count):
        r.s_zrem(ready, call_id)
        r.s_zadd(processing, call_id, expiry)
        out.append(r.s_hget(jobs, call_id) or "")
    return out


CLAIM = Script("voiceops_claim", _CLAIM_LUA, _claim)


# --------------------------------------------------------------------------
# heartbeat: extend a lease, but only if this worker still holds it.
# KEYS: processing      ARGV: call_id, new_expiry_ms
# --------------------------------------------------------------------------
_HEARTBEAT_LUA = """
local processing = KEYS[1]
if redis.call('ZSCORE', processing, ARGV[1]) == false then return 0 end
redis.call('ZADD', processing, tonumber(ARGV[2]), ARGV[1])
return 1
"""


def _heartbeat(r: InMemoryRedis, keys: list[str], args: list[str]) -> int:
    if r.s_zscore(keys[0], args[0]) is None:
        return 0
    r.s_zadd(keys[0], args[0], float(args[1]))
    return 1


HEARTBEAT = Script("voiceops_heartbeat", _HEARTBEAT_LUA, _heartbeat)


# --------------------------------------------------------------------------
# release: drop a call from every queue structure (terminal outcome).
# KEYS: ready, scheduled, processing, jobs, meta     ARGV: call_id
# --------------------------------------------------------------------------
_RELEASE_LUA = """
local id = ARGV[1]
redis.call('ZREM', KEYS[1], id)
redis.call('ZREM', KEYS[2], id)
redis.call('ZREM', KEYS[3], id)
redis.call('HDEL', KEYS[4], id)
redis.call('HDEL', KEYS[5], id)
return 1
"""


def _release(r: InMemoryRedis, keys: list[str], args: list[str]) -> int:
    call_id = args[0]
    for key in keys[:3]:
        r.s_zrem(key, call_id)
    for key in keys[3:]:
        r.s_hdel(key, call_id)
    return 1


RELEASE = Script("voiceops_release", _RELEASE_LUA, _release)


# --------------------------------------------------------------------------
# reclaim: take back leases that expired because a worker died mid-call.
# KEYS: processing, jobs    ARGV: now_ms, limit
# --------------------------------------------------------------------------
_RECLAIM_LUA = """
local processing, jobs = KEYS[1], KEYS[2]
local now, limit = tonumber(ARGV[1]), tonumber(ARGV[2])
local stalled = redis.call('ZRANGEBYSCORE', processing, '-inf', now, 'LIMIT', 0, limit)
local out = {}
for i = 1, #stalled do
  local id = stalled[i]
  redis.call('ZREM', processing, id)
  out[#out + 1] = redis.call('HGET', jobs, id) or ''
end
return out
"""


def _reclaim(r: InMemoryRedis, keys: list[str], args: list[str]) -> list[str]:
    processing, jobs = keys
    now, limit = float(args[0]), int(args[1])
    out: list[str] = []
    for call_id in r.s_zrange_by_score(processing, float("-inf"), now, count=limit):
        r.s_zrem(processing, call_id)
        out.append(r.s_hget(jobs, call_id) or "")
    return out


RECLAIM = Script("voiceops_reclaim", _RECLAIM_LUA, _reclaim)


# --------------------------------------------------------------------------
# dead_letter: move a permanently failed call out of the working set.
# KEYS: ready, scheduled, processing, jobs, meta, dlq, dlq_jobs
# ARGV: call_id, payload_json, now_ms
# --------------------------------------------------------------------------
_DEAD_LETTER_LUA = """
local id, payload, now = ARGV[1], ARGV[2], tonumber(ARGV[3])
redis.call('ZREM', KEYS[1], id)
redis.call('ZREM', KEYS[2], id)
redis.call('ZREM', KEYS[3], id)
redis.call('HDEL', KEYS[4], id)
redis.call('HDEL', KEYS[5], id)
redis.call('ZADD', KEYS[6], now, id)
redis.call('HSET', KEYS[7], id, payload)
return 1
"""


def _dead_letter(r: InMemoryRedis, keys: list[str], args: list[str]) -> int:
    call_id, payload, now = args[0], args[1], float(args[2])
    for key in keys[:3]:
        r.s_zrem(key, call_id)
    for key in keys[3:5]:
        r.s_hdel(key, call_id)
    r.s_zadd(keys[5], call_id, now)
    r.s_hset(keys[6], call_id, payload)
    return 1


DEAD_LETTER = Script("voiceops_dead_letter", _DEAD_LETTER_LUA, _dead_letter)


# --------------------------------------------------------------------------
# requeue_dead: pull a call back out of the dead-letter queue.
# KEYS: dlq, dlq_jobs, jobs, meta, ready    ARGV: call_id, rank, now_ms
# --------------------------------------------------------------------------
_REQUEUE_DEAD_LUA = """
local dlq, dlq_jobs, jobs, meta, ready = KEYS[1], KEYS[2], KEYS[3], KEYS[4], KEYS[5]
local id, rank, now = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3])
local payload = redis.call('HGET', dlq_jobs, id)
if payload == false then return 0 end
redis.call('ZREM', dlq, id)
redis.call('HDEL', dlq_jobs, id)
redis.call('HSET', jobs, id, payload)
redis.call('HSET', meta, id, rank)
redis.call('ZADD', ready, rank * 1e13 + now, id)
return 1
"""


def _requeue_dead(r: InMemoryRedis, keys: list[str], args: list[str]) -> int:
    dlq, dlq_jobs, jobs, meta, ready = keys
    call_id, rank, now = args[0], float(args[1]), float(args[2])
    payload = r.s_hget(dlq_jobs, call_id)
    if payload is None:
        return 0
    r.s_zrem(dlq, call_id)
    r.s_hdel(dlq_jobs, call_id)
    r.s_hset(jobs, call_id, payload)
    r.s_hset(meta, call_id, str(int(rank)))
    r.s_zadd(ready, call_id, rank * PRIORITY_STRIDE + now)
    return 1


REQUEUE_DEAD = Script("voiceops_requeue_dead", _REQUEUE_DEAD_LUA, _requeue_dead)


ALL_SCRIPTS: dict[str, Any] = {
    s.name: s
    for s in (ENQUEUE, PROMOTE, CLAIM, HEARTBEAT, RELEASE, RECLAIM, DEAD_LETTER, REQUEUE_DEAD)
}
