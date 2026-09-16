"""Demo agents used by the seed script and the docker-compose bootstrap."""

from __future__ import annotations

from typing import Any

from app.agents.workflow import DEFAULT_WORKFLOW

FOLLOW_UP_WORKFLOW: dict[str, Any] = {
    "start_node": "intro",
    "nodes": [
        {
            "id": "intro",
            "type": "say",
            "label": "Introduce the call",
            "prompt": (
                "Hi, this is Riley calling from Northwind about your recent delivery. "
                "This will only take a moment."
            ),
            "next": "satisfied",
        },
        {
            "id": "satisfied",
            "type": "branch",
            "label": "Was the delivery OK?",
            "prompt": "Did everything arrive as expected?",
            "reprompt": "Sorry, did the delivery arrive as you expected?",
            "intents": [
                {
                    "name": "yes",
                    "description": "confirms the delivery was fine, correct, right, good",
                    "examples": ["yes", "all good", "that's right"],
                    "next": "thanks",
                },
                {
                    "name": "no",
                    "description": "reports a problem: damaged, broken, missing, wrong item",
                    "examples": ["it arrived broken", "the wrong item came"],
                    "next": "collect_issue",
                },
            ],
            "default": "collect_issue",
        },
        {
            "id": "collect_issue",
            "type": "collect",
            "label": "Capture the issue",
            "prompt": "I'm sorry to hear that. Can you tell me what went wrong?",
            "reprompt": "Could you describe the problem for me?",
            "field": "issue",
            "field_type": "text",
            "max_attempts": 2,
            "next": "escalate",
            "on_failure": "escalate",
        },
        {
            "id": "escalate",
            "type": "transfer",
            "label": "Hand to a specialist",
            "prompt": "Thanks - I'll put you through to someone who can sort that out.",
            "destination": "delivery-queue",
            "outcome": "escalated",
        },
        {
            "id": "thanks",
            "type": "hangup",
            "label": "Close positively",
            "prompt": "That's great to hear. Thanks for your time, and enjoy the rest of your day.",
            "outcome": "resolved",
        },
    ],
}

DEMO_AGENTS: list[dict[str, Any]] = [
    {
        "name": "Ava - Support Triage",
        "description": "Answers inbound support themes and routes to the right queue.",
        "system_prompt": (
            "You are Ava, a calm and concise support agent for Northwind. "
            "Keep replies to one or two sentences."
        ),
        "voice_id": "ava",
        "max_turns": 24,
        "workflow": DEFAULT_WORKFLOW,
    },
    {
        "name": "Riley - Delivery Follow-up",
        "description": "Outbound satisfaction check after a delivery.",
        "system_prompt": (
            "You are Riley, a friendly follow-up agent for Northwind deliveries. "
            "Be brief and respect the customer's time."
        ),
        "voice_id": "riley",
        "max_turns": 16,
        "workflow": FOLLOW_UP_WORKFLOW,
    },
]

# Numbers chosen so a seeded dashboard shows every state the queue can reach.
DEMO_NUMBERS: list[tuple[str, str]] = [
    ("+15551110001111", "always answers"),
    ("+15551110002222", "usually answers"),
    ("+15551110003333", "usually answers"),
    ("+15551110009999", "never answers - exercises retry + dead letter"),
    ("+15551110000000", "invalid number - exercises permanent failure"),
    ("+15551110008888", "busy until the third attempt"),
    ("+15551110007777", "drops mid-conversation"),
]
