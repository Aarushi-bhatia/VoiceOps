# VoiceOps

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

Brings up Postgres, Redis, the API, a worker, and the dashboard on <http://localhost:5173>. Scale the call capacity with `docker compose up -d --scale worker=3`.

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
| LLM | Rule-based intent classification and field extraction | Anthropic Messages API |
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
cd backend && .venv/bin/ruff check app tests && cd ../frontend && npx eslint src
```

The suite covers the queue's ordering/lease/scheduling/dead-letter semantics, the retry policy, workflow validation, the conversation runtime (including mid-call drops and turn limits), the worker end to end, and the HTTP surface. It runs against the in-process queue and SQLite, so it needs no services.

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
- There is no authentication on the API — it assumes a trusted network or a gateway in front.
