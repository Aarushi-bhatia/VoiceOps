#!/usr/bin/env bash
# Start the API for a demo: one process, mocked phone line and speech, real LLM.
#
#   ./run.sh                     start the API on :8000 with Gemini
#   LLM_PROVIDER=mock ./run.sh   fully offline, no API keys needed
#   ./run.sh seed                queue and run a fresh batch of demo calls
#
# Variables set here override .env, so .env can keep real-provider settings.
# The mocked phone line sends no real audio, so speech has to be mocked with it.
set -euo pipefail
cd "$(dirname "$0")"

# No Redis: the queue and the worker live inside the API process.
export REDIS_URL=
export TELEPHONY_PROVIDER=mock STT_PROVIDER=mock TTS_PROVIDER=mock

if [[ "${1:-}" == "seed" ]]; then
  # Seeding runs dozens of conversations; the mock LLM keeps it fast and free.
  # A fresh --seed each time, since the seed script's idempotency keys would
  # otherwise return the calls an earlier run already created.
  LLM_PROVIDER=mock exec .venv/bin/python scripts/seed.py --calls 40 --run --seed "$(date +%s)"
fi

export LLM_PROVIDER="${LLM_PROVIDER:-gemini}"
exec .venv/bin/uvicorn app.main:app --port "${PORT:-8000}"
