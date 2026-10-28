# Live calling with Telnyx: bugs fixed and what to improve

The first real calls through the Telnyx adapter, using an ngrok tunnel to a local API. This records what broke, why, what was fixed, and what is still worth doing. The adapter had only been tested against a fake carrier before this.

## How a real call flows

1. `POST /api/v1/calls` writes the call to the database and the queue.
2. A worker claims it and `POST`s to Telnyx `/v2/calls` with `stream_url = <public base url>/ws/media/<call_id>`, then registers a waiter for that call in `MediaStreamRegistry`.
3. Telnyx opens the audio websocket to `stream_url` and sends `connected`. The API hands the socket to the waiter.
4. When the callee answers, Telnyx sends `start`. The runtime then plays the greeting (ElevenLabs), listens (Deepgram), asks the LLM (Gemini) and speaks the reply.

Containers in `docker compose`: `api` (8000), `worker`, `postgres`, `redis`, `dashboard` (5173).

## Result

A real call now runs end to end: greeting, caller's reply understood, agent's reply spoken, call ended by the agent, outcome `resolved`, with a summary. The reply came about 4 s after the caller stopped speaking, down from 9–10 s.

## Bugs found and fixed

| # | Symptom | Cause | Fix | Where |
| --- | --- | --- | --- | --- |
| 1 | Agent shows "inactive", calls refused | The dashboard's **New agent** creates a draft with `is_active: false`. Inactive agents reject calls. | By design. Tick **Active** on the agent page, or `PATCH` it. | none |
| 2 | Call dead-lettered: Telnyx `403`, code `10010` | The outbound voice profile allowed only `US`/`CA` and the destination was `+91…`. | Allow the destination country in the profile; verify the number on a trial account. | Telnyx portal |
| 3 | Call rings, no audio, log says `media stream arrived with nothing waiting` | `MediaStreamRegistry` is in-process memory. The worker registers its waiter in one container, the websocket arrives at another. | Workaround: run the API and worker in one process (no `REDIS_URL`). Proper fix is below. | run mode (code fix open) |
| 4 | Call cut instantly, `404` from ElevenLabs | A new agent's `voice_id` defaults to `default`. The mock TTS accepts anything, ElevenLabs does not. | Set a real voice id on the agent. On a free ElevenLabs plan only non-library voices work (library voices return `402`). | agent data |
| 5 | Phone rings, then the call is hung up before or as you answer | Telnyx opens the audio socket **when ringing starts** and sends `start` only on answer. The adapter treated the socket attaching as "answered" and gave up after 5 s waiting for `start`. | `dial()` now waits for `start` for the full ring time; if it never comes the call is no-answer and is retried. Two tests added. | `stream.py` |
| 6 | Long silence after speaking, caller hangs up | `GEMINI_MODEL=gemini-3.1-flash-lite` took 9–10 s for a one-word answer; `gemini-2.5-flash` takes about 1.2–1.6 s. A turn makes several LLM requests. | Use a faster model (`GEMINI_MODEL=gemini-2.5-flash`). | config |

Also added: the media stream now logs events it does not handle, bytes sent per reply, how long Telnyx took to echo the `mark`, and how many seconds of caller audio each listen caught. Without these, bugs 5 and 6 were invisible.

## Improvements worth making

Ordered by value.

### 1. Make calls work across containers (fixes bug 3 properly)
Real calls only work with the API and worker in one process (`app.state.embedded_worker = not settings.uses_real_redis`), so `docker compose` with a separate worker cannot take a live call. Twilio has the same limit.

| Option | Effort | Notes |
| --- | --- | --- |
| `EMBEDDED_WORKER` setting so the API can run the worker with Redis and Postgres, and drop the `worker` container from compose | Small | Works in Docker. One process owns all live calls, so no scale-out. |
| API relays audio to the owning worker over Redis | Larger | Keeps workers scalable. Adds a hop of latency per audio frame. |
| Worker serves the media websocket, ingress routes `/ws/media/*` to it | Larger | Lowest latency. Needs sticky routing by `call_id` with more than one worker. |

Do the first now. Build one of the others before running more than one worker.

### 2. Reduce reply latency
- Deepgram is called per utterance as a batch request (about 2–3 s). Streaming transcription would cut this to well under a second.
- The repo default is `gemini-3.8-flash` (`config.py`, `docker-compose.yml`, `.env.example`). It was not measured in this run; the local `.env` used `gemini-3.1-flash-lite`, which was slow. Measure the default and pick a tested one.
- A turn makes several LLM requests (classify, extract, reply). Combining them or running them in parallel saves seconds.
- The end-of-speech silence threshold is 700 ms; tune it per language and line quality.

### 3. Caller audio buffered while the agent speaks
`MediaStreamSession._inbound` is only read in `listen()`, so anything the caller says or any noise during playback is queued and treated as their next reply. Clear the queue before listening, and use Telnyx's `clear` message for barge-in so the agent stops when interrupted. In one call, `listen` reported 11.3 s of audio from an 8 s window, which fits this.

### 4. Catch config mistakes before a call, not during it
- A pre-flight command that checks the Telnyx voice profile's destinations, the from-number's connection, the account balance, and that the agent's `voice_id` synthesizes. Every failure above was a config mistake surfaced only as a failed call.
- Validate an agent's `voice_id` when it is saved, or default it from a provider setting (for example `ELEVENLABS_DEFAULT_VOICE_ID`) instead of the literal `default`.

### 5. Keep the whole carrier error
The failure reason is cut at 200 characters (`stream.py`), which hid Telnyx's `detail` text. Store the full body, or at least the parsed `errors[].detail`. Map "destination not allowed" to a clearer message than `agent_config`.

### 6. Security
- `API_KEYS` is empty and the tunnel is public, so anyone with the URL can place calls on your account. Set it.
- Put `TELEPHONY_ALLOWED_NUMBERS` in `.env` while testing. It is currently only passed per run.
- Telnyx posts call events to the application's webhook URL (the tunnel root), which returns `405` in the logs. Either add a small webhook route or clear the URL in the Telnyx application.

### 7. Smaller things
- `test_real_provider_without_credentials_fails_at_startup` fails when a real `TELNYX_API_KEY` is in `.env`. Set the variable to an empty string in the test instead of deleting it.
- The dashboard's **New agent** draft being inactive is easy to miss; a hint on the Agents page would help.
- README says "143 tests"; there are 176.
- The 5 s timeout when playing audio before `start` ([stream.py](../backend/app/voice/telephony/stream.py)) is a magic number worth naming.

## Running a live test

1. Telnyx: a number on a Call Control application, a voice profile that allows the destination country, a verified destination if on trial, and some balance.
2. `.env`: `TELEPHONY_PROVIDER=telnyx`, the `TELNYX_*` values, real STT/LLM/TTS keys, `TELEPHONY_ALLOWED_NUMBERS=<your number>`, `TELEPHONY_MAX_CALL_SECONDS=60`.
3. An **active** agent with a real ElevenLabs `voice_id`.
4. A public tunnel to the API (`ngrok`), its URL in `TELNYX_PUBLIC_BASE_URL`.
5. Run the API with the worker in the same process: unset `REDIS_URL`, point `DATABASE_URL` at SQLite or the compose Postgres, `uvicorn app.main:app --port 8000`.
6. Place the call from **Calls → New call**, and watch the log for `sent … bytes`, `mark echoed`, `heard … of caller audio`.
