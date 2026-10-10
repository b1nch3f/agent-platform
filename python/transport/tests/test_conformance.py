"""Runs the language-neutral cases in /conformance/transport against this transport."""

import json
from pathlib import Path

import pytest

from ap_contracts import AgentDefinition, ToolSpec
from ap_sdk import Agent
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


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
async def test_transport_conformance(path: Path):
    case = json.loads(path.read_text())
    tools = [StubTool(t) for t in case["tools"]]
    definition = AgentDefinition(
        **case["agent"], tools=[{"name": t.name, "function": f"stub:{t.name}"} for t in tools]
    )
    client, _, _ = make_client(case["model_script"], Agent(definition=definition, tools=tools))
    cursors: dict[str, int] = {}

    for request in case["requests"]:
        conversation_id, expect = request["conversation_id"], request["expect"]
        response = await client.post(f"/conversations/{conversation_id}/messages", json=request["body"])

        assert response.status_code == expect["status"]
        if response.status_code != 200:
            continue

        stream = parse_stream(response.text)
        actual = []
        for sse_id, event in stream:
            data = {k: v for k, v in event["data"].items() if k != "trace_id"}
            actual.append(
                {"type": event["type"]} if event["type"] == "error" else {"type": event["type"], "data": data}
            )
            cursors[conversation_id] = cursors.get(conversation_id, 0) + 1
            assert event["cursor"] == cursors[conversation_id]
            assert sse_id == str(event["cursor"])
            assert event["conversation_id"] == conversation_id
            assert event["message_id"] == request["body"]["message_id"]
        assert actual == expect["events"]
        invocation_ids = {event["invocation_id"] for _, event in stream}
        assert len(invocation_ids) == 1 and all(invocation_ids)
