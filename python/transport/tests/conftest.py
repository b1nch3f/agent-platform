import httpx
import pytest

from ap_contracts import AgentDefinition, OutboundEvent
from ap_sdk import Agent
from ap_sdk.testing import scripted_runtime
from ap_transport.app import create_app


def make_client(turns, agent: Agent | None = None):
    """An HTTP client wired straight to the app, with a scripted model behind it."""
    agent = agent or Agent(
        definition=AgentDefinition(name="test-agent", version="1.0.0", model="fake", instructions="Be brief.")
    )
    runtime, model = scripted_runtime(agent, turns)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runtime)), base_url="http://test")
    return client, runtime, model


def events_of(response: httpx.Response) -> list[OutboundEvent]:
    """The OutboundEvents in a Server-Sent Events response body."""
    return [
        OutboundEvent.model_validate_json(line[5:].lstrip(" "))
        for line in response.text.split("\n")
        if line.startswith("data:")
    ]


@pytest.fixture
def start():
    """Start a conversation with its first message. Returns (conversation_id, response)."""

    async def start(client, message_id: str, text: str) -> tuple[str, httpx.Response]:
        response = await client.post("/conversations", json={"message_id": message_id, "text": text})
        return response.headers.get("x-conversation-id"), response

    return start


@pytest.fixture
def post():
    """Send the next message of an existing conversation."""

    async def post(client, conversation_id: str, message_id: str, text: str) -> httpx.Response:
        return await client.post(
            f"/conversations/{conversation_id}/messages", json={"message_id": message_id, "text": text}
        )

    return post
