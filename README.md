# VoiceOps

[![CI](https://github.com/Aarushi-bhatia/VoiceOps/actions/workflows/ci.yml/badge.svg)](https://github.com/Aarushi-bhatia/VoiceOps/actions/workflows/ci.yml)

A voice AI platform for automating customer-support calls. It wires **speech-to-text → LLM → text-to-speech** into a configurable agent workflow, schedules and retries the calls through a **Redis-backed queue**, stores everything in **PostgreSQL**, and gives CX operations a **React dashboard** to configure agents, watch calls, and diagnose failures.

Every provider defaults to a deterministic mock, so the whole system — including real conversations with a simulated customer — runs end to end with **no API keys and no phone line**.

---

## Quick start

No Docker, Postgres or Redis needed:

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
cd backend && .venv/bin/python scripts/seed.py --calls 40 --run
```

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Then open <http://localhost:5173>. The API is at <http://localhost:8000>, with interactive docs at `/docs`.

With no `REDIS_URL` set the queue lives inside the API process, so the API **embeds a worker** automatically — otherwise nothing would consume the queue. Set `REDIS_URL` and the worker becomes a separate, horizontally scalable process.

### With Docker

```bash
docker compose up --build
```

Brings up Postgres, Redis, the API, a worker, and the dashboard on <http://localhost:5173>. Scale the call capacity with `docker compose up -d --scale worker=3`. Compose sets the calling policy explicitly — carrier rate limit and calling hours — since those are per-deployment and default to off.

### On Kubernetes

```bash
kubectl apply -f k8s/
```

Sixteen objects: Postgres and Redis as StatefulSets with persistent volumes, the API and dashboard as Deployments behind an Ingress, and workers as a Deployment with a HorizontalPodAutoscaler (3 to 20 pods on CPU). See [k8s/README.md](k8s/README.md) for the decisions behind it, including why workers get a 120-second termination grace period.

## Throughput

Measured with `scripts/loadtest.py`, which queues a batch of calls, drains them, and reports what actually happened. This exercises the queue, worker pool, conversation engine and database on one machine against the mock voice stack, so it measures the platform rather than a carrier's round-trip time.

| Calls | Worker concurrency | Throughput | Calls/hour | Turn latency (p50 / p95) |
| --- | --- | --- | --- | --- |
| 200 | 4 | 2.1 calls/sec | 7,700 | 173 ms / 268 ms |
| 200 | 16 | 7.9 calls/sec | 28,500 | 173 ms / 268 ms |
| 200 | 32 | 15.2 calls/sec | 54,600 | 173 ms / 265 ms |
| **10,000** | **48** | **26.0 calls/sec** | **93,700** | **173 ms / 268 ms** |

Throughput scales close to linearly with worker concurrency, and per-turn latency is identical at 200 calls and at 10,000 — so the queue and lease machinery are not the bottleneck at this scale. The 10,000-call run (against real Redis) completed in 6m24s with no failures, recording 62,302 conversation turns.

The real ceiling is the carrier, not the platform: a telephony provider typically allows around one call per second per number, so the constraint is numbers and per-number rate limiting rather than worker capacity. That rate limiting is not yet implemented.

Reproduce with:

```bash
cd backend && python scripts/loadtest.py --calls 500 --concurrency 32
```

---

## How a call flows

```
POST /api/v1/calls
      │
      ▼
  ┌────────┐   scheduled_at in the future?   ┌──────────────┐
  │  API   │ ──────────────────────────────► │ q:scheduled  │
  └────────┘                                 └──────┬───────┘
      │ else                                        │ due
      ▼                                             ▼
  ┌──────────────────────────────────────────────────────┐
  │  q:ready  — sorted by priority band, then FIFO        │
  └───────────────────────┬──────────────────────────────┘
                          │ atomic claim + lease
                          ▼
  ┌───────────────────────────────────────────────────────┐
  │ Worker                                                 │
  │   telephony.dial ──► answered? ──► ConversationRuntime │
  │        │ no                              │             │
  │        ▼                                 ▼             │
  │   RetryPolicy                    STT → LLM → TTS loop  │
  │   transient → backoff → q:scheduled      │             │
  │   permanent / exhausted → q:dlq          ▼             │
  │                                   turns + outcome → DB │
  └───────────────────────────────────────────────────────┘
```

A worker holds a **lease** on each call it is running and heartbeats it. If the worker dies, the lease expires and another worker reclaims the call rather than leaving it stranded.

---

## Admission control and backpressure

Workers pull only what their concurrency allows, so a slow worker simply stops claiming and the backlog stays in Redis rather than piling up in memory. On the producer side four gates apply:

| Gate | Behaviour |
| --- | --- |
| **Queue depth** (`MAX_QUEUE_DEPTH`) | New calls are refused with `429 Too Many Requests` and a `Retry-After` header once the backlog reaches the cap |
| **Staleness** (`MAX_CALL_AGE_SECONDS`) | A call queued hours ago is dropped rather than placed. A support call answered six hours late is worse than no call |
| **Carrier rate limit** (`CARRIER_CALLS_PER_SECOND`) | A token bucket per outbound number, shared across workers. Carriers allow roughly one call per second per number, so this — not worker capacity — is the real ceiling |
| **Calling hours** (`CALLING_HOURS_*`) | Calls outside the window are deferred to the next one, honouring a per-call `timezone` in metadata |

The last two **defer** rather than fail: nothing went wrong with the call, so they must not consume a retry attempt.

Suppressed numbers are refused at creation *and* re-checked before dialling, since the do-not-call list can change while a call waits.

## The queue

Redis structures, all under the `voiceops:` namespace:

| Key | Type | Purpose |
| --- | --- | --- |
| `q:ready` | zset | Claimable calls, scored `priority_rank × 10¹³ + enqueued_ms` — priority band first, FIFO inside it |
| `q:scheduled` | zset | Calls parked until `scheduled_at` (a future call, or a retry backing off) |
| `q:processing` | zset | Leased calls, scored by lease expiry |
| `q:jobs` / `q:meta` | hash | Job payload and priority rank |
| `q:dlq` / `q:dlq:jobs` | zset/hash | Calls that gave up |
| `stats:*` | int | Counters the dashboard reads |
| `events` | pub/sub | Live event stream for the dashboard |

Every multi-step operation (claim, promote, reclaim, dead-letter) is a **Lua script**, so it is atomic across workers. Each script ships with a Python transliteration next to it in `app/queue/scripts.py`, which the in-process fallback runs under a lock — that is what makes the whole platform work without a Redis server.

### Retries

`RetryPolicy` backs off exponentially with symmetric jitter, so a burst of simultaneous failures doesn't produce a synchronised retry spike:

```
delay = min(base × multiplier^(attempt-1), max_delay) ± jitter_ratio
15s → 30s → 60s → 120s …   (defaults, ±20%)
```

Failures are classified, and the classification decides the behaviour:

| Transient — retried | Permanent — dead-lettered immediately |
| --- | --- |
| `network`, `timeout`, `provider_error`, `rate_limited`, `no_answer`, `busy`, `unknown` | `invalid_number`, `do_not_call`, `agent_config`, `canceled` |

A line that drops **after** the customer answered is not retried: the conversation state is gone, and redialling to restart from the greeting is worse for the customer than leaving it for a human. It is recorded as a completed call with the outcome `customer_hung_up`.

If Redis is flushed or restarted, the database is the source of truth: on startup both the API and the worker rebuild the queue from any call still in a non-terminal state (`app/services/recovery.py`).

---

## Agent workflows

An agent is a persona (system prompt, voice, language, limits) plus a **conversation graph**. Five node types:

| Type | Behaviour |
| --- | --- |
| `say` | Speak a line, continue to `next` |
| `collect` | Ask for a value, extract it from the reply, re-ask up to `max_attempts`, then fall through to `on_failure` |
| `branch` | Ask an open question, classify the reply into one of several intents, re-ask on no match, then fall through to `default` |
| `transfer` | Hand the call to a human queue |
| `hangup` | End the call with a business outcome |

Prompts render `{placeholders}` from call metadata and values collected earlier in the same call — `"I have order {order_id}. Is that correct?"`.

Graphs are validated strictly on save: duplicate ids, dangling edges, unreachable nodes and graphs with no terminal node are all rejected, because each of those would otherwise surface halfway through a live call. Cycles *are* allowed — the per-agent turn and duration limits bound them.

`POST /api/v1/agents/{id}/simulate` runs a workflow against a simulated customer through the mock stack. No call is placed and nothing is charged; the dashboard exposes it as **Test conversation**.

---

## Providers

The runtime only talks to four protocols (`app/voice/base.py`), so the stack is swappable per component:

| Component | `mock` (default) | Real adapter |
| --- | --- | --- |
| STT | Deterministic, replays the mock audio's text | Deepgram |
| LLM | Rule-based intent classification and field extraction | Anthropic Messages API, or Google Gemini |
| TTS | Silent PCM sized to a real speaking rate | ElevenLabs |
| Telephony | Simulated customer with scripted personas | Twilio (REST + bidirectional Media Streams) |

Switch one at a time — `LLM_PROVIDER=anthropic` with everything else mocked is a valid, useful configuration.

The Anthropic adapter is written against the current API: no `temperature` (current models reject it), `output_config.effort: "low"` to keep latency down on a live call, and structured output via `output_config.format` so every NLU reply is guaranteed to parse. SDK exceptions are mapped onto the retry categories above.

Twilio requires `TWILIO_PUBLIC_BASE_URL` to be an origin Twilio can reach (a tunnel in development): the call's TwiML opens a `<Connect><Stream>` back to this service's `/ws/twilio/{call_id}`, and the API hands that socket to the waiting call.

### Mock telephony fixtures

Reserved number suffixes make each queue path reproducible — useful for demos and used by the tests:

| Ends with | Behaviour |
| --- | --- |
| `1111` | Always answers, no random failures |
| `9999` | Never answers — retries, then dead-letters |
| `8888` | Busy until the third attempt, then answers |
| `0000` | Invalid number — permanent failure, no retries |
| `7777` | Answers, then the line drops mid-conversation |
| anything else | ~8% no answer, ~3% busy, otherwise answered |

---

## Authentication

`API_KEYS` is `<key>:<role>,<key>:<role>` with three roles:

| Role | Can |
| --- | --- |
| `viewer` | Read calls, transcripts, queue state and analytics |
| `operator` | + queue, cancel and retry calls; run a simulation |
| `admin` | + edit agents, change the do-not-call list, purge the dead-letter queue |

They are separate because transcripts hold personal data and an agent's workflow scripts what customers hear — those should not be the same permission. Present the key as `X-API-Key` or `Authorization: Bearer`.

With no keys configured, authentication is disabled, which keeps local development and the test suite free of ceremony. `/health` reports `auth: enabled|disabled`, so an accidentally unprotected deployment is visible rather than silent.

## Operations

**Migrations.** Alembic, with the URL taken from application settings so it always targets the same database as the app.

```bash
cd backend && alembic upgrade head
```

In Kubernetes this is a Job (`k8s/migrate-job.yaml`) run once per release, not per pod. `init_models()` still creates tables on startup for local development and the SQLite fallback.

**Retention.** Transcripts are personal data, so finished calls are deleted on a schedule (nightly CronJob, `RETENTION_DAYS`, default 90):

```bash
cd backend && python scripts/retention.py --dry-run
```

**Metrics.** `GET /metrics` exposes Prometheus text: queue depth by state, lifetime queue events, calls by status/outcome/failure category, turn-latency quantiles, and in-flight calls. The two worth alerting on are `voiceops_dead_letter_calls` and a rising `voiceops_queue_depth{state="ready"}`.

## API

`GET /docs` has the full interactive reference. The shape of it:

```
GET    /health                     GET    /health/ready
POST   /api/v1/agents              GET    /api/v1/agents
GET    /api/v1/agents/{id}         PATCH  /api/v1/agents/{id}       DELETE /api/v1/agents/{id}
GET    /api/v1/agents/{id}/stats   POST   /api/v1/agents/{id}/simulate

POST   /api/v1/calls               POST   /api/v1/calls/bulk
GET    /api/v1/calls               GET    /api/v1/calls/{id}
POST   /api/v1/calls/{id}/cancel   POST   /api/v1/calls/{id}/retry

GET    /api/v1/queue/stats         GET    /api/v1/queue/dead-letter
POST   /api/v1/queue/dead-letter/{id}/requeue
DELETE /api/v1/queue/dead-letter/{id}

GET    /api/v1/analytics/overview  GET /api/v1/analytics/timeseries  GET /api/v1/analytics/agents

GET    /api/v1/suppression         POST /api/v1/suppression
GET    /api/v1/suppression/{number}  DELETE /api/v1/suppression/{number}

GET    /metrics                    (Prometheus)
WS     /ws/events                  WS  /ws/twilio/{call_id}
```

`POST /api/v1/calls` accepts an `idempotency_key`, so retrying a batch upload never double-dials a customer.

---

## Dashboard

React + JavaScript (Vite), TanStack Query, Recharts.

- **Dashboard** — connect and resolution rates, call volume, outcome and failure breakdowns, response latency, live worker events
- **Calls** — filterable table; a drawer shows the transcript with per-turn STT/LLM/TTS latency, the path taken through the workflow, collected values, and cancel/retry
- **Agents** — structured workflow editor (with a JSON escape hatch) that mirrors the server's graph validation, plus **Test conversation**
- **Queue** — depth, lifetime counters, and the dead-letter queue with requeue/purge
- **Analytics** — the same measures over a selectable window, with per-agent performance

Charts use a colourblind-validated categorical palette, restepped rather than flipped for dark mode; status is never carried by colour alone (every badge has a glyph and a word).

---

## Configuration

Copy `.env.example` to `.env`. Everything has a working default; the ones that matter:

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | SQLite file | Set to `postgresql+asyncpg://…` in production |
| `REDIS_URL` | *(unset)* | Unset ⇒ in-process queue and an embedded worker |
| `WORKER_CONCURRENCY` | `4` | Simultaneous calls per worker process |
| `QUEUE_LEASE_SECONDS` | `120` | Heartbeated; expiry means the call is reclaimed |
| `RETRY_MAX_ATTEMPTS` | `4` | Per-call override via `max_attempts` |
| `RETRY_BASE_DELAY_SECONDS` | `15` | With `RETRY_BACKOFF_MULTIPLIER`, `RETRY_MAX_DELAY_SECONDS`, `RETRY_JITTER_RATIO` |
| `STT/LLM/TTS/TELEPHONY_PROVIDER` | `mock` | Plus each provider's API key |

---

## Development

```bash
cd backend && .venv/bin/python -m pytest -q
```

```bash
cd backend && .venv/bin/ruff check app tests scripts && cd ../frontend && npx eslint src
```

The suite covers the queue's ordering/lease/scheduling/dead-letter semantics, the retry policy, workflow validation, the conversation runtime (including mid-call drops and turn limits), the worker end to end, and the HTTP surface. By default it runs against the in-process queue and SQLite, so it needs no services.

The same suite runs against the real backends by pointing two environment variables at them:

```bash
cd backend && VOICEOPS_TEST_REDIS_URL=redis://localhost:6379/15 VOICEOPS_TEST_DATABASE_URL=postgresql+asyncpg://voiceops:voiceops@localhost:5432/voiceops_test .venv/bin/python -m pytest -q
```

That matters because both alternatives are more forgiving than the real thing. The in-process queue runs Python transliterations rather than the Lua scripts, and SQLite autocommits DDL and does not enforce the CHECK constraints. Two bugs hid behind exactly those differences: a lease leak that caused duplicate calls, and an Alembic configuration that reported success while creating nothing on Postgres.

CI runs all three configurations on every push — in-process, real Redis, and Postgres — plus migrations on both databases, the shutdown test against a real server, the seed and retention scripts, and the dashboard lint and build.

### Layout

```
backend/app/
  agents/     workflow graph, NLU tasks, conversation runtime
  api/        FastAPI routes
  core/       settings, logging, enums
  db/         models, session, portable column types
  queue/      Redis backends, Lua scripts, the queue, retry policy
  services/   call lifecycle, analytics, simulation, queue recovery
  voice/      provider protocols + mock and real adapters
  worker/     the worker loop
k8s/          Kubernetes manifests (namespace, config, datastores, workloads)
.github/      CI: lint, tests on both queue backends, dashboard build
frontend/src/
  components/ layout, tables, charts, workflow editor, transcript
  hooks/      data fetching, event stream, theme
  pages/      dashboard, calls, agents, queue, analytics
```

---

## Notes and limits

- `init_models()` creates tables on startup, which is fine for development and the SQLite fallback. A production deployment should run migrations (Alembic) instead; the models carry an explicit naming convention so generated constraint names stay stable.
- The in-process queue is single-process by design. A standalone worker refuses to start without `REDIS_URL` rather than silently polling an empty queue.
- Cost figures are a per-call estimate from a rate table in `app/services/calls.py`. Replace the constants with your contracted rates.
- The Kubernetes manifests have not been applied to a live cluster. The Docker images build and the full Compose stack runs against Postgres and Redis, but the manifests themselves are unproven.
