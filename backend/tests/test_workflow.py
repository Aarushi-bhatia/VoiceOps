"""Workflow graph validation and prompt rendering."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents.workflow import DEFAULT_WORKFLOW, Workflow, render


def test_default_workflow_is_valid():
    workflow = Workflow.model_validate(DEFAULT_WORKFLOW)
    assert workflow.start.id == "greeting"
    assert workflow.reachable_ids() == {n.id for n in workflow.nodes}
    assert any(n.is_terminal for n in workflow.nodes)


@pytest.mark.parametrize(
    ("bad", "expected"),
    [
        (
            {"start_node": "a", "nodes": [{"id": "a", "type": "say", "prompt": "hi", "next": "b"}]},
            "undefined nodes",
        ),
        ({"start_node": "missing", "nodes": [{"id": "a", "type": "hangup"}]}, "is not defined"),
        (
            {
                "start_node": "a",
                "nodes": [
                    {"id": "a", "type": "say", "prompt": "hi", "next": "z"},
                    {"id": "z", "type": "hangup"},
                    {"id": "orphan", "type": "hangup"},
                ],
            },
            "unreachable",
        ),
        (
            {"start_node": "a", "nodes": [{"id": "a", "type": "say", "prompt": "hi"}]},
            "transfer or hangup",
        ),
        (
            {
                "start_node": "a",
                "nodes": [
                    {"id": "a", "type": "hangup"},
                    {"id": "a", "type": "hangup"},
                ],
            },
            "duplicate node ids",
        ),
    ],
)
def test_invalid_graphs_are_rejected(bad, expected):
    with pytest.raises(ValidationError, match=expected):
        Workflow.model_validate(bad)


def test_duplicate_intent_names_are_rejected():
    with pytest.raises(ValidationError, match="duplicate intent"):
        Workflow.model_validate(
            {
                "start_node": "b",
                "nodes": [
                    {
                        "id": "b",
                        "type": "branch",
                        "intents": [
                            {"name": "same", "next": "end"},
                            {"name": "same", "next": "end"},
                        ],
                        "default": "end",
                    },
                    {"id": "end", "type": "hangup"},
                ],
            }
        )


def test_cycles_are_allowed_because_turn_limits_bound_them():
    workflow = Workflow.model_validate(
        {
            "start_node": "ask",
            "nodes": [
                {
                    "id": "ask",
                    "type": "branch",
                    "prompt": "Anything else?",
                    "intents": [{"name": "yes", "next": "ask"}],
                    "default": "bye",
                },
                {"id": "bye", "type": "hangup"},
            ],
        }
    )
    assert workflow.node("ask").targets() == ["ask", "bye"]


def test_render_fills_known_placeholders_and_leaves_the_rest():
    assert render("Order {order_id} for {name}", {"order_id": "ORD-1"}) == (
        "Order ORD-1 for {name}"
    )


def test_render_survives_a_stray_brace():
    assert render("100% sure {", {}) == "100% sure {"
