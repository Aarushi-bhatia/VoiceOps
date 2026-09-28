# VoiceOps

A voice AI platform for automating customer-support calls. Speech-to-text, an LLM, and text-to-speech run over a configurable agent workflow; a Redis-backed queue schedules and retries the calls; a React dashboard lets CX configure agents and see what happened.

Every provider has a deterministic mock, so the whole system — real conversations included — runs with no API keys and no phone line.

## Quick start

```bash
docker compose up --build
```

Postgres, Redis, the API, a worker and the dashboard, on <http://localhost:5173>.

Without Docker, it falls back to SQLite and an in-process queue:

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python scripts/seed.py --calls 40 --run
.venv/bin/uvicorn app.main:app --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

With no `REDIS_URL`, the API embeds a worker, since nothing else would consume the queue.

## How a call flows

```
POST /calls ─► database ─► Redis queue ─► worker ─► dial ─► conversation ─► database
                                            │                    │
                                            └─ fails ─► retry or dead letter
```

The **database is written before the queue**. If the queue write fails, recovery re-queues from the database on startup; the reverse would leave a job pointing at nothing.

A worker **leases** each call and heartbeats it. If the worker dies the lease expires and another picks the call up. Claiming is a Lua script so two workers can never take the same call — which in this domain means phoning someone twice.

## Retries

Failures are classified, and the classification decides what happens:

- **Transient** (busy, no answer, network, timeout, rate limited) → exponential backoff with jitter: 15s, 30s, 60s, 120s ±20%. The jitter stops a provider outage producing a synchronised retry spike on recovery.
- **Permanent** (invalid number, do-not-call, misconfigured agent, expired) → straight to a dead-letter queue for a human.
- **Anything after the customer answered** → never redialled, whatever caused it. They have already been interrupted; ringing back to replay the greeting is worse than leaving it for a person.

## Backpressure

Workers pull only what their concurrency allows, so nothing overflows. On the producer side: a queue-depth cap returning `429`, a staleness limit that drops calls too old to be worth placing, a per-number carrier rate limit (a token bucket in Redis — the carrier, not worker capacity, is the real ceiling), and a calling-hours window. The last two **defer** rather than fail, so they don't consume a retry attempt.

## Agent workflows

An agent is a persona plus a graph of five node types: `say`, `collect`, `branch`, `transfer`, `hangup`. Prompts render `{placeholders}` from values collected earlier in the same call.

**The graph decides what happens, not the model.** The LLM is given three narrow jobs — classify a reply into an intent, extract a field, write the closing summary — each with a required response schema. That keeps it predictable, testable and cheap.

Graphs are validated on save: dangling edges, unreachable steps and graphs with no ending are rejected, because each would otherwise surface mid-call.

`POST /agents/{id}/simulate` runs a workflow against a simulated customer. No call, no cost.

## Providers

| | Mock (default) | Real |
| --- | --- | --- |
| LLM | keyword matching | Google Gemini, Anthropic |
| STT | replays the mock audio's text | Deepgram |
| TTS | silent PCM sized to a speaking rate | ElevenLabs |
| Telephony | scripted customer | Twilio (REST + Media Streams) |

Switch one at a time — `LLM_PROVIDER=gemini` with the rest mocked is a normal setup, and the one this has been run on most. The LLM adapters absorb the provider's own rate limits and overloads with backoff rather than abandoning a call mid-conversation.

Reserved mock numbers make each queue path reproducible: `…1111` always answers, `…9999` never does, `…8888` is busy until the third attempt, `…0000` is invalid, `…7777` drops mid-call.

## Configuration

Copy `.env.example` to `.env`. Everything has a working default.

| | |
| --- | --- |
| `DATABASE_URL` | SQLite by default; Postgres in deployment |
| `REDIS_URL` | Unset ⇒ in-process queue and an embedded worker |
| `WORKER_CONCURRENCY` | Simultaneous calls per worker |
| `API_KEYS` | `key:role,…` with roles `viewer`/`operator`/`admin`. Empty disables auth, which `/health` reports |
| `*_PROVIDER` | `mock` by default, plus each provider's key |
| `CARRIER_CALLS_PER_SECOND`, `CALLING_HOURS_*` | Per-deployment policy, off by default |
| `RETENTION_DAYS` | Transcripts are personal data; finished calls are deleted on a schedule |

Full API reference at `/docs`. Prometheus metrics at `/metrics`.

## Development

```bash
cd backend && .venv/bin/python -m pytest -q
```

145 tests, no services needed. The same suite runs against the real backends:

```bash
VOICEOPS_TEST_REDIS_URL=redis://localhost:6379/15 \
VOICEOPS_TEST_DATABASE_URL=postgresql+asyncpg://voiceops:voiceops@localhost:5432/voiceops_test \
.venv/bin/python -m pytest -q
```

That matters: the in-process queue runs Python stand-ins rather than the Lua scripts, and SQLite autocommits DDL and skips CHECK constraints. Two bugs hid behind exactly those differences — a lease leak that caused duplicate calls, and an Alembic setup that reported success while creating nothing on Postgres. CI runs all three configurations, plus migrations on both databases.

```bash
cd backend && .venv/bin/alembic upgrade head          # migrations
cd backend && .venv/bin/python scripts/loadtest.py    # throughput
cd backend && .venv/bin/python scripts/retention.py --dry-run
```

Measured at **26 calls/sec with 48 workers** on one machine (10,000 calls, no failures, per-turn latency flat versus a 200-call run). The real ceiling is the carrier: roughly one call per second per number.

## What isn't done

- Never deployed. The Docker images build and the Compose stack runs against Postgres, but the Kubernetes manifests in `k8s/` have not been applied to a cluster.
- STT, TTS and telephony have only ever run as mocks. They are interdependent — transcription needs real audio, which needs real telephony, which needs a paid number. The LLM has been run for real.
- STT and TTS go one utterance per HTTP request rather than streaming, which is the main source of per-turn latency.
- No inbound calls, no barge-in, no do-not-call list beyond the local one.
