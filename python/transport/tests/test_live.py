"""One end-to-end check against the real model. Off by default; run with `uv run pytest -m live`."""

from pathlib import Path

import httpx
import pytest

from ap_gateway import ModelGateway, ToolGateway
from ap_sdk import InMemoryConversationStore, Runtime, load_agent
from ap_transport.app import create_app
from conftest import events_of

EXAMPLE_AGENT = Path(__file__).parents[3] / "agents" / "example" / "agent.yaml"


@pytest.mark.live
async def test_example_agent_answers_with_the_real_model():
    from dotenv import find_dotenv, load_dotenv

    from ap_gateway.anthropic_adapter import AnthropicAdapter

    load_dotenv(find_dotenv(usecwd=True), override=True)  # same key the server uses

    runtime = Runtime(
        agent=load_agent(EXAMPLE_AGENT),
        model_gateway=ModelGateway(default=AnthropicAdapter()),
        tool_gateway=ToolGateway(),
        store=InMemoryConversationStore(),
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://test")

    first = events_of(
        await client.post(
            "/conversations/live/messages",
            json={"message_id": "m1", "text": "What is the weather in Pune right now?"},
            timeout=120,
        )
    )
    second = events_of(
        await client.post(
            "/conversations/live/messages",
            json={"message_id": "m2", "text": "Which city did I just ask about? One word."},
            timeout=120,
        )
    )

    assert first[-1].type == "turn_done", first[-1].data
    statuses = [(e.data["tool"], e.data["status"]) for e in first if e.type == "tool_status"]
    assert statuses == [("get_weather", "started"), ("get_weather", "completed")]
    assert "29" in "".join(e.data["text"] for e in first if e.type == "token")
    # The second turn only works if the first, including its tool call, was replayed to the model.
    assert second[-1].type == "turn_done", second[-1].data
    assert "pune" in "".join(e.data["text"] for e in second if e.type == "token").lower()
