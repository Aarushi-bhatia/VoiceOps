"""Enumerations shared across the DB models, API schemas and the queue."""

from __future__ import annotations

from enum import StrEnum


class CallStatus(StrEnum):
    QUEUED = "queued"  # waiting in the ready queue
    SCHEDULED = "scheduled"  # waiting for scheduled_at to arrive
    DIALING = "dialing"  # claimed by a worker, placing the call
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"  # terminal failure, retries exhausted or permanent
    RETRYING = "retrying"  # transient failure, waiting on backoff
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in {CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.CANCELED}


class CallOutcome(StrEnum):
    """Business result of a completed conversation."""

    RESOLVED = "resolved"
    ESCALATED = "escalated"  # transferred to a human
    CALLBACK_SCHEDULED = "callback_scheduled"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"
    CUSTOMER_HUNG_UP = "customer_hung_up"
    UNRESOLVED = "unresolved"


class CallPriority(StrEnum):
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

    @property
    def rank(self) -> int:
        return _PRIORITY_RANK[self]


_PRIORITY_RANK = {
    CallPriority.URGENT: 0,
    CallPriority.HIGH: 1,
    CallPriority.NORMAL: 2,
    CallPriority.LOW: 3,
}


class FailureCategory(StrEnum):
    """Drives the retry decision: transient categories back off and retry."""

    NETWORK = "network"  # transient
    PROVIDER_ERROR = "provider_error"  # transient
    RATE_LIMITED = "rate_limited"  # transient
    TIMEOUT = "timeout"  # transient
    NO_ANSWER = "no_answer"  # transient (person may pick up later)
    BUSY = "busy"  # transient
    INVALID_NUMBER = "invalid_number"  # permanent
    DO_NOT_CALL = "do_not_call"  # permanent
    AGENT_CONFIG = "agent_config"  # permanent
    CANCELED = "canceled"  # permanent
    UNKNOWN = "unknown"  # transient, conservatively

    @property
    def is_transient(self) -> bool:
        return self not in _PERMANENT


_PERMANENT = {
    FailureCategory.INVALID_NUMBER,
    FailureCategory.DO_NOT_CALL,
    FailureCategory.AGENT_CONFIG,
    FailureCategory.CANCELED,
}


class TurnRole(StrEnum):
    AGENT = "agent"
    CUSTOMER = "customer"
    SYSTEM = "system"


class NodeType(StrEnum):
    SAY = "say"  # speak a line, move on
    COLLECT = "collect"  # speak a prompt, capture a value from the reply
    BRANCH = "branch"  # speak a prompt, classify the reply into an intent
    TRANSFER = "transfer"  # hand off to a human queue/number
    HANGUP = "hangup"  # end the call with an outcome


class EventType(StrEnum):
    ENQUEUED = "enqueued"
    CLAIMED = "claimed"
    DIALING = "dialing"
    ANSWERED = "answered"
    TURN = "turn"
    NODE_ENTERED = "node_entered"
    TRANSFERRED = "transferred"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTERED = "dead_lettered"
    CANCELED = "canceled"
