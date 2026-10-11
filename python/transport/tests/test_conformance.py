"""Runs the language-neutral cases in /conformance/transport against this transport."""

import json
from pathlib import Path

import pytest

from ap_contracts import AgentDefinition, ToolSpec
from ap_sdk import Agent
from ap_sdk.testing import scripted_runtime
from ap_transport.dispatcher import DuplicateMessage, UnknownConversation
from ap_transport.local import LocalClient
from conftest import make_client

CASES = sorted((Path(__file__).parents[3] / "conformance" / "transport").glob("*.json"))


class StubTool:
    """A tool whose result is fixed by the case."""

    def __init__(self, case_tool: dict):
        self.name = case_tool["name"]
        self._case = case_tool

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name, description=self._case["description"], input_schema=self._case["input_schema"]
        )

    async def handler(self, arguments: dict) -> str:
        if "error" in self._case:
            raise RuntimeError(self._case["error"])
        return self._case["result"]


def parse_stream(text: str) -> list[tuple[str, dict]]:
    """(SSE id, event data) for each event in the stream."""
    events = []
    for chunk in filter(None, text.split("\n\n")):
        fields = dict(line.split(": ", 1) for line in chunk.split("\n"))
        events.append((fields["id"], json.loads(fields["data"])))
    return events


def test_cases_exist():
    assert CASES, "no conformance cases found"


def agent_for(case: dict) -> Agent:
    tools = [StubTool(t) for t in case["tools"]]
    definition = AgentDefinition(
        **case["agent"], tools=[{"name": t.name, "function": f"stub:{t.name}"} for t in tools]
    )
    return Agent(definition=definition, tools=tools)


class Checker:
    """The checks every implementation must make (see conformance/README.md)."""

    def __init__(self) -> None:
        self.issued: dict[str, str] = {}  # label -> conversation ID the transport returned
        self.cursors: dict[str, int] = {}
        self.invocation_ids: set[str] = set()

    def check(
        self, request: dict, conversation_id: str, events: list[dict], sse_ids: list[str] | None
    ) -> None:
        actual = []
        for index, event in enumerate(events):
            data = {k: v for k, v in event["data"].items() if k != "trace_id"}
            actual.append(
                {"type": event["type"]} if event["type"] == "error" else {"type": event["type"], "data": data}
            )
            self.cursors[conversation_id] = self.cursors.get(conversation_id, 0) + 1
            assert event["cursor"] == self.cursors[conversation_id]
            if sse_ids is not None:
                assert sse_ids[index] == str(event["cursor"])
            assert event["conversation_id"] == conversation_id
            assert event["message_id"] == request["body"]["message_id"]
            assert event["invocation_type"] == "agent"
        assert actual == request["expect"]["events"]
        invocation_ids = {event["invocation_id"] for event in events}
        assert len(invocation_ids) == 1 and all(invocation_ids)
        assert not invocation_ids & self.invocation_ids  # a new invocation for every request
        self.invocation_ids |= invocation_ids


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
async def test_transport_conformance(path: Path):
    """Each case over HTTP."""
    case = json.loads(path.read_text())
    client, _, _ = make_client(case["model_script"], agent_for(case))
    checker = Checker()

    for request in case["requests"]:
        label, expect = request.get("conversation"), request["expect"]
        if label is not None and label not in checker.issued:
            response = await client.post("/conversations", json=request["body"])
            if response.status_code == 201:
                checker.issued[label] = response.headers["x-conversation-id"]
        else:
            conversation_id = checker.issued[label] if label is not None else request["conversation_id"]
            response = await client.post(f"/conversations/{conversation_id}/messages", json=request["body"])

        assert response.status_code == expect["status"]
        if response.status_code not in (200, 201):
            continue
        stream = parse_stream(response.text)
        checker.check(
            request,
            checker.issued[label],
            [event for _, event in stream],
            [sse_id for sse_id, _ in stream],
        )


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_local_conformance(path: Path):
    """The same cases through the in-process client, where a rejected request is an exception."""
    case = json.loads(path.read_text())
    runtime, _ = scripted_runtime(agent_for(case), case["model_script"])
    client = LocalClient(runtime)
    checker = Checker()

    for request in case["requests"]:
        label, body, expect = request.get("conversation"), request["body"], request["expect"]
        try:
            if label is not None and label not in checker.issued:
                checker.issued[label], events = client.start(body["text"], message_id=body["message_id"])
                status = 201
            else:
                conversation_id = checker.issued[label] if label is not None else request["conversation_id"]
                events = client.send(conversation_id, body["text"], message_id=body["message_id"])
                status = 200
            events = [event.model_dump() for event in events]
        except UnknownConversation:
            status = 404
        except DuplicateMessage:
            status = 409

        assert status == expect["status"]
        if status in (200, 201):
            checker.check(request, checker.issued[label], events, None)
