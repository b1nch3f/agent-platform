"""Helpers for testing an agent with a scripted (fake) model. No API key, no network.

runtime, model = scripted_runtime(agent, [
    [{"tool_call": {"name": "get_weather", "arguments": {"city": "Pune"}}}],
    [{"text": "It is 29°C in Pune."}],
])
events = await run_turn(runtime, "Weather in Pune?")
assert text_of(events) == "It is 29°C in Pune."
"""

from __future__ import annotations

import uuid
from typing import Any

from ap_contracts import CallIdentity, TurnEnvelope
from ap_gateway import ModelGateway, ScriptedModel, ToolGateway
from ap_sdk.agent import Agent
from ap_sdk.runtime import Runtime, RuntimeEvent
from ap_sdk.store import ConversationStore, InMemoryConversationStore


def scripted_runtime(
    agent: Agent, turns: list[list[dict[str, Any]]], *, store: ConversationStore | None = None
) -> tuple[Runtime, ScriptedModel]:
    """A runtime whose model replays `turns` (see ScriptedModel for the step format).
    The returned model records every request it received in `model.requests`."""
    model = ScriptedModel(turns)
    runtime = Runtime(
        agent=agent,
        model_gateway=ModelGateway(default=model),
        tool_gateway=ToolGateway(),
        store=store or InMemoryConversationStore(),
    )
    return runtime, model


def make_envelope(text: str, *, conversation_id: str = "c1", message_id: str | None = None) -> TurnEnvelope:
    return TurnEnvelope(
        message_id=message_id or uuid.uuid4().hex,
        conversation_id=conversation_id,
        session_id="test-session",
        invocation_id=uuid.uuid4().hex,
        protocol="http_sse",
        identity=CallIdentity(tenant="test", user_id="test-user"),
        kind="message",
        payload={"text": text},
    )


async def run_turn(
    runtime: Runtime, text: str, *, conversation_id: str = "c1", message_id: str | None = None
) -> list[RuntimeEvent]:
    """Run one turn to the end and return its events."""
    envelope = make_envelope(text, conversation_id=conversation_id, message_id=message_id)
    return [event async for event in runtime.run_turn(envelope)]


def text_of(events: list[RuntimeEvent]) -> str:
    """The reply text: all token events joined."""
    return "".join(e.data["text"] for e in events if e.type == "token")


def tool_statuses(events: list[RuntimeEvent]) -> list[tuple[str, str]]:
    """(tool, status) for each tool_status event, in order."""
    return [(e.data["tool"], e.data["status"]) for e in events if e.type == "tool_status"]
