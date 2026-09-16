"""The configurable conversation graph an agent follows.

A workflow is a set of nodes plus a start node. Each node speaks a line and
(except for terminal nodes) names where to go next:

``say``       speak, then continue
``collect``   ask for a value, capture it, re-ask on failure
``branch``    ask an open question, classify the answer into one of several intents
``transfer``  hand the call to a human
``hangup``    end the call with a business outcome

Validation is strict on purpose: a dangling ``next`` is a configuration bug that
would otherwise surface halfway through a live call.
"""

from __future__ import annotations

import string
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.enums import CallOutcome, NodeType

NODE_ID = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class _NodeBase(BaseModel):
    id: str = NODE_ID
    # Free-form label shown in the dashboard's workflow editor.
    label: str | None = Field(default=None, max_length=120)

    def targets(self) -> list[str]:
        raise NotImplementedError

    @property
    def is_terminal(self) -> bool:
        return False


class SayNode(_NodeBase):
    type: Literal[NodeType.SAY] = NodeType.SAY
    prompt: str = Field(min_length=1)
    next: str | None = None

    def targets(self) -> list[str]:
        return [self.next] if self.next else []


class CollectNode(_NodeBase):
    type: Literal[NodeType.COLLECT] = NodeType.COLLECT
    prompt: str = Field(min_length=1)
    field: str = Field(min_length=1, max_length=64)
    # Drives extraction: order_id, email, phone, number, date, zip, boolean, text.
    field_type: str = "text"
    reprompt: str | None = None
    max_attempts: int = Field(default=2, ge=1, le=5)
    next: str | None = None
    # Where to go when the value could not be captured within max_attempts.
    on_failure: str | None = None

    def targets(self) -> list[str]:
        return [t for t in (self.next, self.on_failure) if t]


class Intent(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    examples: list[str] = Field(default_factory=list)
    next: str = NODE_ID


class BranchNode(_NodeBase):
    type: Literal[NodeType.BRANCH] = NodeType.BRANCH
    prompt: str | None = None
    intents: list[Intent] = Field(min_length=1)
    default: str = NODE_ID
    # Classifications below this confidence fall through to ``default``.
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # An unmatched or silent reply is re-asked with ``reprompt`` this many times
    # before routing to ``default`` - callers mishear the first question often.
    max_attempts: int = Field(default=2, ge=1, le=5)
    reprompt: str | None = None

    def targets(self) -> list[str]:
        return [i.next for i in self.intents] + [self.default]

    @model_validator(mode="after")
    def _unique_intents(self) -> BranchNode:
        names = [i.name for i in self.intents]
        if len(names) != len(set(names)):
            raise ValueError(f"node '{self.id}' has duplicate intent names")
        return self


class TransferNode(_NodeBase):
    type: Literal[NodeType.TRANSFER] = NodeType.TRANSFER
    prompt: str | None = None
    destination: str = Field(min_length=1, max_length=64)
    outcome: CallOutcome = CallOutcome.ESCALATED

    def targets(self) -> list[str]:
        return []

    @property
    def is_terminal(self) -> bool:
        return True


class HangupNode(_NodeBase):
    type: Literal[NodeType.HANGUP] = NodeType.HANGUP
    prompt: str | None = None
    outcome: CallOutcome = CallOutcome.RESOLVED

    def targets(self) -> list[str]:
        return []

    @property
    def is_terminal(self) -> bool:
        return True


Node = Annotated[
    SayNode | CollectNode | BranchNode | TransferNode | HangupNode,
    Field(discriminator="type"),
]


class Workflow(BaseModel):
    start_node: str = NODE_ID
    nodes: list[Node] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_graph(self) -> Workflow:
        ids = [n.id for n in self.nodes]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate node ids: {sorted(duplicates)}")

        known = set(ids)
        if self.start_node not in known:
            raise ValueError(f"start_node '{self.start_node}' is not defined")

        dangling = [
            f"{node.id} -> {target}"
            for node in self.nodes
            for target in node.targets()
            if target not in known
        ]
        if dangling:
            raise ValueError(f"edges point at undefined nodes: {sorted(dangling)}")

        if not any(n.is_terminal for n in self.nodes):
            raise ValueError("workflow needs at least one transfer or hangup node")

        unreachable = known - self.reachable_ids()
        if unreachable:
            raise ValueError(f"unreachable nodes: {sorted(unreachable)}")
        return self

    def reachable_ids(self) -> set[str]:
        by_id = {n.id: n for n in self.nodes}
        seen: set[str] = set()
        stack = [self.start_node]
        while stack:
            current = stack.pop()
            if current in seen or current not in by_id:
                continue
            seen.add(current)
            stack.extend(by_id[current].targets())
        return seen

    def node(self, node_id: str) -> Node:
        for candidate in self.nodes:
            if candidate.id == node_id:
                return candidate
        raise KeyError(f"no node '{node_id}' in workflow")

    @property
    def start(self) -> Node:
        return self.node(self.start_node)


class _SafeFormatter(string.Formatter):
    """Leaves unknown placeholders intact instead of raising mid-call."""

    def get_value(self, key: Any, args: Any, kwargs: Any) -> Any:
        if isinstance(key, str):
            return kwargs.get(key, "{" + key + "}")
        return super().get_value(key, args, kwargs)


_FORMATTER = _SafeFormatter()


def render(template: str, context: dict[str, Any]) -> str:
    """Fill ``{placeholders}`` from call metadata and collected values."""
    try:
        return _FORMATTER.vformat(template, (), context)
    except (ValueError, IndexError):
        # A stray brace in a prompt should not break the call.
        return template


DEFAULT_WORKFLOW: dict[str, Any] = {
    "start_node": "greeting",
    "nodes": [
        {
            "id": "greeting",
            "type": "branch",
            "label": "Greeting & triage",
            "prompt": (
                "Hi, you've reached Northwind support, this is Ava. What can I help you with today?"
            ),
            "reprompt": "Sorry, I didn't quite get that. What can I help you with?",
            "intents": [
                {
                    "name": "refund",
                    "description": "wants a refund, money back, or to return a damaged item",
                    "examples": ["I want a refund", "the item arrived broken"],
                    "next": "collect_order",
                },
                {
                    "name": "delivery_status",
                    "description": "asking where a package or delivery is, tracking a shipment",
                    "examples": ["where is my package", "my delivery hasn't arrived"],
                    "next": "collect_order",
                },
                {
                    "name": "billing",
                    "description": "a charge, invoice, payment or subscription question",
                    "examples": ["I was billed twice", "there's a charge I don't recognise"],
                    "next": "transfer_billing",
                },
            ],
            "default": "transfer_human",
        },
        {
            "id": "collect_order",
            "type": "collect",
            "label": "Capture order number",
            "prompt": "Sure, I can help with that. What's your order number?",
            "reprompt": "Sorry, I didn't catch that. Could you read out your order number again?",
            "field": "order_id",
            "field_type": "order_id",
            "max_attempts": 2,
            "next": "confirm",
            "on_failure": "transfer_human",
        },
        {
            "id": "confirm",
            "type": "branch",
            "label": "Confirm the order",
            "prompt": "Thanks. I have order {order_id}. Is that correct?",
            "reprompt": "Sorry - can you confirm, is order {order_id} the right one?",
            "intents": [
                {
                    "name": "yes",
                    "description": "confirms that the details are correct",
                    "examples": ["yes", "that's right", "correct"],
                    "next": "resolve",
                },
                {
                    "name": "no",
                    "description": "says the details are wrong or incorrect",
                    "examples": ["no", "that's wrong", "not right"],
                    "next": "transfer_human",
                },
            ],
            "default": "transfer_human",
        },
        {
            "id": "resolve",
            "type": "say",
            "label": "Confirm resolution",
            "prompt": (
                "Great - I've logged this against order {order_id} and "
                "you'll get a confirmation email within the hour."
            ),
            "next": "goodbye",
        },
        {
            "id": "goodbye",
            "type": "hangup",
            "label": "Close the call",
            "prompt": "Thanks for calling Northwind. Have a good day.",
            "outcome": "resolved",
        },
        {
            "id": "transfer_billing",
            "type": "transfer",
            "label": "Transfer to billing",
            "prompt": "Let me put you through to our billing team.",
            "destination": "billing-queue",
            "outcome": "escalated",
        },
        {
            "id": "transfer_human",
            "type": "transfer",
            "label": "Transfer to an agent",
            "prompt": "I'll pass you to one of my colleagues who can help.",
            "destination": "support-queue",
            "outcome": "escalated",
        },
    ],
}
