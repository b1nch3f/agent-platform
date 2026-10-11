"""The in-process client and the terminal chat loop, with a scripted model."""

import io

import pytest

from ap_contracts import AgentDefinition
from ap_sdk import Agent, tool
from ap_sdk.testing import scripted_runtime
from ap_transport.cli import run_chat
from ap_transport.dispatcher import DuplicateMessage, UnknownConversation
from ap_transport.local import LocalClient


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    return "29C"


def make_local(turns, *tools) -> tuple[LocalClient, object]:
    definition = AgentDefinition(
        name="test-agent",
        version="1.0.0",
        model="fake",
        instructions="Be brief.",
        tools=[{"name": t.name, "function": f"tools:{t.name}"} for t in tools],
    )
    runtime, model = scripted_runtime(Agent(definition=definition, tools=list(tools)), turns)
    return LocalClient(runtime), model


def test_local_start_issues_a_conversation_id_and_streams_the_turn():
    client, model = make_local(
        [
            [{"tool_call": {"id": "tc1", "name": "get_weather", "arguments": {"city": "Pune"}}}],
            [{"text": "29C."}],
        ],
        get_weather,
    )

    conversation_id, stream = client.start("Weather?", message_id="m1")
    events = list(stream)

    assert len(conversation_id) == 32
    assert [(e.cursor, e.type) for e in events] == [
        (1, "tool_status"),
        (2, "tool_status"),
        (3, "token"),
        (4, "turn_done"),
    ]
    assert {(e.conversation_id, e.message_id, e.invocation_type) for e in events} == {
        (conversation_id, "m1", "agent")
    }
    assert len({e.invocation_id for e in events}) == 1 and events[0].invocation_id
    assert model.requests[0].context.invocation_id == events[0].invocation_id


def test_local_events_arrive_one_at_a_time():
    client, _ = make_local([[{"text": "a"}, {"text": "b"}]])

    _, stream = client.start("go")
    first = next(stream)

    assert first.data == {"text": "a"}  # delivered before the turn has finished
    assert [e.type for e in stream] == ["token", "turn_done"]


def test_local_conversation_continues_and_cursor_carries_on():
    client, model = make_local([[{"text": "Hi Sam."}], [{"text": "Sam."}]])
    conversation_id, stream = client.start("I am Sam.")
    list(stream)

    second = list(client.send(conversation_id, "Who am I?"))

    assert [e.cursor for e in second] == [3, 4]
    assert [m.content[0].text for m in model.requests[1].messages] == ["I am Sam.", "Hi Sam.", "Who am I?"]


def test_local_rejects_a_conversation_id_it_did_not_issue():
    client, model = make_local([[{"text": "a"}]])

    with pytest.raises(UnknownConversation):
        client.send("c1", "hello")
    assert model.requests == []


def test_local_rejects_a_repeated_message_id():
    client, model = make_local([[{"text": "a"}]])
    conversation_id, stream = client.start("one", message_id="m1")
    list(stream)

    with pytest.raises(DuplicateMessage):
        client.send(conversation_id, "again", message_id="m1")
    assert len(model.requests) == 1


def test_local_turn_is_marked_with_the_local_protocol():
    client, _ = make_local([[{"text": "a"}]])
    seen = []
    client._dispatcher.runtime.root_agent.callbacks.before_agent.append(
        lambda ctx: seen.append(ctx.envelope.protocol)
    )

    list(client.start("go")[1])

    assert seen == ["local"]


def scripted_input(*lines: str):
    remaining = list(lines)

    def read(prompt: str) -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    return read


def test_chat_loop_prints_the_reply_and_tool_activity():
    client, _ = make_local(
        [
            [{"text": "Checking. "}, {"tool_call": {"name": "get_weather", "arguments": {"city": "Pune"}}}],
            [{"text": "It is 29C."}],
            [{"text": "Bye."}],
        ],
        get_weather,
    )
    out = io.StringIO()

    run_chat(client, read=scripted_input("Weather in Pune?", "", "Thanks"), out=out)

    assert out.getvalue() == (
        "agent> Checking. \n  [get_weather: started]\n  [get_weather: completed]\nIt is 29C.\nagent> Bye.\n\n"
    )


def test_chat_loop_shows_a_failed_turn_and_keeps_going():
    client, _ = make_local([[{"error": "provider down"}], [{"text": "Back."}]])
    out = io.StringIO()

    run_chat(client, read=scripted_input("one", "two"), out=out)

    assert out.getvalue() == "agent> \n  [error: RuntimeError: provider down]\nagent> Back.\n\n"


def test_local_turn_logs_no_tracing_errors(caplog):
    """Spans opened during a turn must be closed in the same task, or the tracing library logs an error."""
    client, _ = make_local(
        [[{"tool_call": {"name": "get_weather", "arguments": {"city": "Pune"}}}], [{"text": "29C."}]],
        get_weather,
    )

    with caplog.at_level("ERROR"):
        list(client.start("Weather?")[1])

    assert [r.getMessage() for r in caplog.records] == []


def test_local_stream_can_be_abandoned_midway():
    client, _ = make_local([[{"text": "a"}, {"text": "b"}], [{"text": "next"}]])

    conversation_id, stream = client.start("go")
    next(stream)
    stream.close()

    assert [e.type for e in client.send(conversation_id, "again")][-1] == "turn_done"
