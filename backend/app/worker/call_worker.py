"""The worker loop: claim calls, run them, and apply the retry policy.

One worker runs up to ``worker_concurrency`` calls at a time. Each tick it

1. promotes calls whose scheduled time has arrived,
2. reclaims leases abandoned by a crashed worker,
3. claims as many calls as it has free capacity for,

then runs each claimed call in its own task with a lease heartbeat alongside it.
Scaling out is a matter of running more worker processes against the same Redis.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from app.agents.runtime import AgentConfig, ConversationRuntime
from app.agents.workflow import Workflow
from app.core.config import Settings, get_settings
from app.core.enums import CallStatus, FailureCategory
from app.db.models import Agent, Call
from app.db.session import session_scope
from app.queue.call_queue import CallQueue, QueuedCall, get_queue
from app.queue.retry import RetryPolicy
from app.services.calls import CallService
from app.voice.base import DialRequest, TelephonyError, VoiceProviderError, VoiceStack
from app.voice.registry import get_voice_stack

logger = logging.getLogger(__name__)


class CallWorker:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        queue: CallQueue | None = None,
        stack: VoiceStack | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.queue = queue or get_queue()
        self.stack = stack or get_voice_stack(self.settings)
        self.retry_policy = RetryPolicy.from_settings(self.settings)
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._running: dict[str, asyncio.Task[None]] = {}
        self._stopping = asyncio.Event()

    @property
    def capacity(self) -> int:
        return max(self.settings.worker_concurrency - len(self._running), 0)

    async def run_forever(self) -> None:
        logger.info(
            "worker started",
            extra={
                "worker_id": self.worker_id,
                "concurrency": self.settings.worker_concurrency,
                **self.stack.describe(),
            },
        )
        interval = self.settings.worker_poll_interval_ms / 1000
        try:
            while not self._stopping.is_set():
                try:
                    await self.tick()
                except Exception:
                    logger.exception("worker tick failed", extra={"worker_id": self.worker_id})
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=interval)
        finally:
            await self._drain()

    async def tick(self) -> int:
        """One scheduling pass. Returns how many calls were started."""
        await self.queue.promote_due()
        await self._recover_stalled()

        if self.capacity == 0:
            return 0
        jobs = await self.queue.claim(self.capacity)
        for job in jobs:
            task = asyncio.create_task(self._run_call(job), name=f"call:{job.call_id}")
            self._running[job.call_id] = task
            task.add_done_callback(lambda _t, cid=job.call_id: self._running.pop(cid, None))
        return len(jobs)

    async def stop(self, *, drain: bool = True) -> None:
        self._stopping.set()
        if not drain:
            for task in list(self._running.values()):
                task.cancel()
        await self._drain()

    async def _drain(self) -> None:
        if self._running:
            await asyncio.gather(*list(self._running.values()), return_exceptions=True)

    async def _recover_stalled(self) -> None:
        """A lease that expired means a worker died mid-call - treat it as a failure."""
        for job in await self.queue.reclaim_stalled():
            await self._handle_failure(
                job,
                category=FailureCategory.TIMEOUT,
                reason="worker lease expired before the call finished",
            )

    # ---------------------------- running a call ----------------------------

    async def _run_call(self, job: QueuedCall) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(job.call_id))
        try:
            await self._execute(job)
        except asyncio.CancelledError:
            raise
        except VoiceProviderError as exc:
            await self._handle_failure(job, category=exc.category, reason=str(exc))
        except Exception as exc:
            logger.exception("unexpected failure running call", extra={"call_id": job.call_id})
            await self._handle_failure(job, category=FailureCategory.UNKNOWN, reason=str(exc))
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, call_id: str) -> None:
        """Keep the lease alive so a long call is not reclaimed underneath us."""
        interval = max(self.settings.queue_lease_seconds / 3, 5)
        while True:
            await asyncio.sleep(interval)
            if not await self.queue.heartbeat(call_id):
                logger.warning("lost queue lease mid-call", extra={"call_id": call_id})
                return

    async def _execute(self, job: QueuedCall) -> None:
        attempt = job.attempt + 1
        async with session_scope() as session:
            service = CallService(session, self.queue)
            call = await session.get(Call, uuid.UUID(job.call_id))
            if call is None:
                logger.warning("queued call has no database row", extra={"call_id": job.call_id})
                await self.queue.release(job.call_id, counter="failed")
                return
            if CallStatus(call.status) is CallStatus.CANCELED:
                await self.queue.release(job.call_id, counter="canceled")
                return

            agent = await session.get(Agent, call.agent_id)
            if agent is None:
                raise VoiceProviderError(
                    f"agent {call.agent_id} no longer exists",
                    category=FailureCategory.AGENT_CONFIG,
                    provider="voiceops",
                )
            config = build_agent_config(agent)
            await service.mark_dialing(call, attempt)
            call_context = {"to_number": call.to_number, **(call.call_metadata or {})}
            agent_id = call.agent_id

        # Dialling and the conversation happen outside the DB transaction so a
        # multi-minute call does not hold a connection open.
        try:
            session_handle = await self.stack.telephony.dial(
                DialRequest(
                    call_id=job.call_id,
                    to_number=job.to_number,
                    attempt=job.attempt,
                    metadata=call_context,
                )
            )
        except TelephonyError as exc:
            await self._handle_failure(job, category=exc.category, reason=str(exc))
            return

        async def emit(name: str, payload: dict) -> None:
            await self.queue.publish_event(
                {
                    "type": name,
                    "call_id": job.call_id,
                    "agent_id": str(agent_id),
                    "payload": payload,
                }
            )

        async with session_scope() as session:
            service = CallService(session, self.queue)
            call = await service.get_call(uuid.UUID(job.call_id))
            await service.mark_answered(call, session_handle.external_id)

        runtime = ConversationRuntime(self.stack, config, on_event=emit)
        result = await runtime.run(session_handle, context=call_context)

        async with session_scope() as session:
            service = CallService(session, self.queue)
            call = await service.get_call(uuid.UUID(job.call_id))
            call.attempt = attempt
            await service.record_result(call, result)

        await self.queue.release(job.call_id, counter="completed")
        logger.info(
            "call completed",
            extra={
                "call_id": job.call_id,
                "outcome": str(result.outcome),
                "turns": len(result.turns),
                "attempt": attempt,
                "worker_id": self.worker_id,
            },
        )

    # ------------------------------ failures -------------------------------

    async def _handle_failure(
        self, job: QueuedCall, *, category: FailureCategory, reason: str
    ) -> None:
        attempt = job.attempt + 1
        decision = self.retry_policy.decide(
            attempt=attempt, category=category, max_attempts=job.max_attempts
        )

        async with session_scope() as session:
            service = CallService(session, self.queue)
            call = await session.get(Call, uuid.UUID(job.call_id))
            if call is not None:
                await service.record_failure(
                    call,
                    category=category,
                    reason=reason,
                    attempt=attempt,
                    retry_at=decision.retry_at,
                    dead_lettered=not decision.should_retry,
                )

        job.last_error = reason
        if decision.should_retry and decision.retry_at is not None:
            # schedule_retry advances job.attempt to `attempt` itself.
            await self.queue.schedule_retry(job, decision.retry_at)
            logger.info(
                "retry scheduled",
                extra={
                    "call_id": job.call_id,
                    "attempt": attempt,
                    "category": str(category),
                    "delay_seconds": round(decision.delay_seconds, 1),
                },
            )
        else:
            job.attempt = attempt
            await self.queue.dead_letter(job, f"{category}: {reason}")
            await self.queue.incr_counter("failed")


def build_agent_config(agent: Agent) -> AgentConfig:
    return AgentConfig(
        name=agent.name,
        workflow=Workflow.model_validate(agent.workflow),
        system_prompt=agent.system_prompt or "",
        llm_model=agent.llm_model,
        temperature=agent.temperature,
        voice_id=agent.voice_id,
        language=agent.language,
        max_turns=agent.max_turns,
        max_call_seconds=agent.max_call_seconds,
    )
