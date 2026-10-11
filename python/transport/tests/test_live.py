"""One end-to-end check against the real model. Off by default; run with `uv run pytest -m live`."""

from pathlib import Path

import httpx
import pytest

from ap_transport.app import create_app
from conftest import events_of

EXAMPLE_AGENT = Path(__file__).parents[3] / "agents" / "example" / "agent.yaml"
MODELS = Path(__file__).parents[2] / "models.yaml"


@pytest.mark.live
async def test_example_agent_answers_with_the_real_model():
    from ap_transport.cli import build_runtime

    # The example agent's model, through the registry and the key in .env, as the CLI runs it.
    runtime = build_runtime(str(EXAMPLE_AGENT), str(MODELS))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://test")

    started = await client.post(
        "/conversations",
        json={"message_id": "m1", "text": "What is the weather in Pune right now?"},
        timeout=120,
    )
    conversation_id = started.headers["x-conversation-id"]
    first = events_of(started)
    second = events_of(
        await client.post(
            f"/conversations/{conversation_id}/messages",
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
