"""Graceful shutdown with a live-events websocket still connected.

This needs a real server: a test client closes the websocket for you on the way
out, which is precisely the case that always worked. The bug only appears when
the client is still attached when the signal arrives.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
PORT = 8137


async def _graceful_shutdown_seconds(tmp_path: Path) -> float:
    websockets = pytest.importorskip("websockets")

    env = {
        **os.environ,
        "REDIS_URL": "",
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'shutdown.db'}",
        "MOCK_LATENCY_SCALE": "0",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(PORT),
            "--log-level",
            "error",
        ],
        cwd=BACKEND,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(120):
            try:
                async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/events"):
                    break
            except Exception:
                await asyncio.sleep(0.25)
        else:
            pytest.skip("server did not start")

        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/events"):
            # Give the handler time to block waiting for its first message.
            await asyncio.sleep(0.5)
            started = time.perf_counter()
            process.send_signal(signal.SIGTERM)
            for _ in range(200):
                if process.poll() is not None:
                    return time.perf_counter() - started
                await asyncio.sleep(0.1)
            return float("inf")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


@pytest.mark.slow
async def test_a_connected_dashboard_does_not_stall_shutdown(tmp_path):
    """Uvicorn drains open connections *before* running lifespan shutdown.

    So a websocket that waits to be told to stop deadlocks: the instruction only
    arrives once the connection it is blocking on has closed. The app chains onto
    the SIGTERM handler instead, which is what breaks the cycle.

    Left unfixed this stalled every rolling deploy until the pod's termination
    grace period expired and Kubernetes killed it.
    """
    elapsed = await _graceful_shutdown_seconds(tmp_path)
    assert elapsed < 5.0, (
        f"shutdown took {elapsed:.1f}s with a websocket connected - it is blocking again"
    )
