import asyncio

import pytest

from ap_contracts import AgentDefinition, TextBlock, ToolCallBlock, ToolCallResult, ToolResultBlock
from ap_sdk import Agent, Callbacks, InMemoryConversationStore, tool
from ap_sdk.callbacks import HOOKS
from ap_sdk.testing import run_turn, scripted_runtime, text_of, tool_statuses


@tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@tool
async def fail(reason: str) -> str:
    """Always fails."""
    raise RuntimeError(reason)


def make_agent(*tools, callbacks: Callbacks | None = None, **overrides) -> Agent:
    definition = AgentDefinition(
        name="test-agent",
        version="1.0.0",
        model="fake",
        instructions="Be brief.",
        tools=[{"name": t.name, "function": f"tools:{t.name}"} for t in tools],
        **overrides,
    )
    return Agent(definition=definition, tools=list(tools), callbacks=callbacks or Callbacks())


def call(name: str, **arguments) -> dict:
    return {"tool_call": {"name": name, "arguments": arguments}}


async def test_text_turn_streams_tokens_and_stores_both_messages():
    runtime, model = scripted_runtime(make_agent(), [[{"text": "Hel"}, {"text": "lo"}]])

    events = await run_turn(runtime, "Hi")

    assert [e.type for e in events] == ["token", "token", "turn_done"]
    assert text_of(events) == "Hello"
    assert events[-1].data == {"stop_reason": "end_turn"}
    stored = await runtime.store.load("c1")
    assert [(m.role, m.content) for m in stored] == [
        ("user", [TextBlock(text="Hi")]),
        ("assistant", [TextBlock(text="Hello")]),  # deltas merged into one block
    ]
    assert model.requests[0].system == "Be brief."
    assert model.requests[0].messages == stored[:1]


async def test_tool_call_runs_through_the_gateway_and_result_goes_back_to_the_model():
    runtime, model = scripted_runtime(
        make_agent(add), [[{"text": "Adding. "}, call("add", a=2, b=3)], [{"text": "It is 5."}]]
    )

    events = await run_turn(runtime, "2+3?")

    assert [e.type for e in events] == ["token", "tool_status", "tool_status", "token", "turn_done"]
    assert tool_statuses(events) == [("add", "started"), ("add", "completed")]
    assert text_of(events) == "Adding. It is 5."
    assert model.requests[0].tools[0].name == "add"
    # The second model call sees the tool call and its result.
    assistant, result = model.requests[1].messages[1:]
    assert assistant.content[1] == ToolCallBlock(id="call_0_1", name="add", arguments={"a": 2, "b": 3})
    assert result.role == "user"
    assert result.content == [ToolResultBlock(tool_call_id="call_0_1", content="5", is_error=False)]


async def test_parallel_tool_calls_return_all_results_in_one_message():
    runtime, model = scripted_runtime(
        make_agent(add), [[call("add", a=1, b=1), call("add", a=2, b=2)], [{"text": "done"}]]
    )

    events = await run_turn(runtime, "go")

    assert tool_statuses(events) == [
        ("add", "started"),
        ("add", "started"),
        ("add", "completed"),
        ("add", "completed"),
    ]
    results = model.requests[1].messages[-1].content
    assert [(r.tool_call_id, r.content) for r in results] == [("call_0_0", "2"), ("call_0_1", "4")]


async def test_failing_tool_is_reported_to_the_model_and_the_turn_continues():
    runtime, model = scripted_runtime(
        make_agent(fail), [[call("fail", reason="broken")], [{"text": "Sorry."}]]
    )

    events = await run_turn(runtime, "go")

    assert tool_statuses(events) == [("fail", "started"), ("fail", "failed")]
    assert events[-1].type == "turn_done"
    result = model.requests[1].messages[-1].content[0]
    assert (result.is_error, result.content) == (True, "RuntimeError: broken")


async def test_invalid_arguments_do_not_run_the_tool():
    ran = []

    @tool
    def record(n: int) -> str:
        """Record a number."""
        ran.append(n)
        return "ok"

    runtime, model = scripted_runtime(
        make_agent(record), [[call("record", n="not a number")], [{"text": "x"}]]
    )

    await run_turn(runtime, "go")

    assert ran == []
    result = model.requests[1].messages[-1].content[0]
    assert result.is_error and "ValidationError" in result.content


async def test_unknown_tool_is_an_error_result():
    runtime, model = scripted_runtime(make_agent(), [[call("nope")], [{"text": "x"}]])

    events = await run_turn(runtime, "go")

    assert tool_statuses(events) == [("nope", "started"), ("nope", "failed")]
    assert model.requests[1].messages[-1].content[0].content == "Unknown tool 'nope'"


async def test_truncated_output_does_not_run_its_tool_call():
    ran = []

    @tool
    def record(n: int) -> str:
        """Record a number."""
        ran.append(n)
        return "ok"

    runtime, model = scripted_runtime(
        make_agent(record), [[call("record", n=1), {"stop_reason": "max_tokens"}], [{"text": "retry"}]]
    )

    events = await run_turn(runtime, "go")

    assert ran == []
    assert tool_statuses(events) == []
    result = model.requests[1].messages[-1].content[0]
    assert result.is_error and "max_tokens" in result.content


async def test_max_steps_stops_the_loop():
    turns = [[call("add", a=1, b=1)] for _ in range(5)]
    runtime, model = scripted_runtime(make_agent(add, max_steps=2), turns)

    events = await run_turn(runtime, "go")

    assert len(model.requests) == 2
    assert events[-1].data == {"stop_reason": "max_steps"}


async def test_second_turn_sees_the_first():
    runtime, model = scripted_runtime(make_agent(), [[{"text": "Hi Sam."}], [{"text": "Sam."}]])

    await run_turn(runtime, "I am Sam.")
    await run_turn(runtime, "Who am I?")

    texts = [(m.role, m.content[0].text) for m in model.requests[1].messages]
    assert texts == [("user", "I am Sam."), ("assistant", "Hi Sam."), ("user", "Who am I?")]


async def test_conversations_are_separate():
    runtime, model = scripted_runtime(make_agent(), [[{"text": "a"}], [{"text": "b"}]])

    await run_turn(runtime, "one", conversation_id="c1")
    await run_turn(runtime, "two", conversation_id="c2")

    assert [m.content[0].text for m in model.requests[1].messages] == ["two"]


async def test_duplicate_message_id_is_an_error_and_calls_no_model():
    runtime, model = scripted_runtime(make_agent(), [[{"text": "a"}]])

    await run_turn(runtime, "one", message_id="m1")
    events = await run_turn(runtime, "one again", message_id="m1")

    assert [e.type for e in events] == ["error"]
    assert "DuplicateMessageError" in events[0].data["message"]
    assert len(model.requests) == 1


async def test_user_message_is_stored_before_the_model_is_called():
    store = InMemoryConversationStore()
    seen = []

    async def before_model(ctx, request):
        seen.append([m.content[0].text for m in await store.load("c1")])

    runtime, _ = scripted_runtime(
        make_agent(callbacks=Callbacks(before_model=[before_model])), [[{"text": "a"}]], store=store
    )

    await run_turn(runtime, "Hi")

    assert seen == [["Hi"]]


def recording_callbacks() -> tuple[Callbacks, list[str]]:
    calls: list[str] = []
    callbacks = Callbacks()
    for hook in HOOKS:
        getattr(callbacks, hook).append(lambda *args, hook=hook: calls.append(hook))
    return callbacks, calls


async def test_all_eight_callbacks_fire_in_order():
    callbacks, calls = recording_callbacks()
    runtime, _ = scripted_runtime(
        make_agent(add, callbacks=callbacks), [[call("add", a=1, b=2)], [{"text": "3"}]]
    )

    await run_turn(runtime, "go")

    assert calls == [
        "before_runtime",
        "before_agent",
        "before_model",
        "after_model",
        "before_tool",
        "after_tool",
        "before_model",
        "after_model",
        "after_agent",
        "after_runtime",
    ]


async def test_model_failure_fires_error_callbacks_and_ends_with_an_error_event():
    callbacks, calls = recording_callbacks()
    runtime, _ = scripted_runtime(
        make_agent(callbacks=callbacks), [[{"text": "partial"}, {"error": "provider down"}]]
    )

    events = await run_turn(runtime, "go")

    assert [e.type for e in events] == ["token", "error"]
    assert events[-1].data == {"message": "RuntimeError: provider down"}
    assert calls == [
        "before_runtime",
        "before_agent",
        "before_model",
        "on_model_error",
        "on_agent_error",
        "on_runtime_error",
    ]


async def test_before_model_can_replace_the_request():
    def shorten(ctx, request):
        return request.model_copy(update={"max_tokens": 7})

    runtime, model = scripted_runtime(
        make_agent(callbacks=Callbacks(before_model=[shorten])), [[{"text": "a"}]]
    )

    await run_turn(runtime, "go")

    assert model.requests[0].max_tokens == 7
    assert len(model.requests[0].messages) == 1  # context assembly ran first


async def test_before_tool_can_answer_instead_of_the_tool():
    def deny(ctx, request):
        return ToolCallResult(tool_call_id=request.tool_call_id, content="Not allowed.", is_error=True)

    runtime, model = scripted_runtime(
        make_agent(fail, callbacks=Callbacks(before_tool=[deny])),
        [[call("fail", reason="x")], [{"text": "ok"}]],
    )

    events = await run_turn(runtime, "go")

    assert tool_statuses(events)[-1] == ("fail", "failed")
    assert model.requests[1].messages[-1].content[0].content == "Not allowed."


async def test_after_tool_can_replace_the_result():
    def redact(ctx, request, result):
        return result.model_copy(update={"content": "[redacted]"})

    runtime, model = scripted_runtime(
        make_agent(add, callbacks=Callbacks(after_tool=[redact])), [[call("add", a=1, b=1)], [{"text": "ok"}]]
    )

    await run_turn(runtime, "go")

    assert model.requests[1].messages[-1].content[0].content == "[redacted]"


async def test_failing_tool_callback_fails_the_tool_call_not_the_turn():
    callbacks, calls = recording_callbacks()

    def explode(ctx, request):
        raise ValueError("bad callback")

    callbacks.before_tool.append(explode)
    runtime, model = scripted_runtime(
        make_agent(add, callbacks=callbacks), [[call("add", a=1, b=1)], [{"text": "ok"}]]
    )

    events = await run_turn(runtime, "go")

    assert events[-1].type == "turn_done"
    assert "on_tool_error" in calls
    assert model.requests[1].messages[-1].content[0].content == "ValueError: bad callback"


async def test_async_callbacks_are_awaited():
    seen = []

    async def before_agent(ctx):
        await asyncio.sleep(0)
        seen.append(ctx.agent.name)

    runtime, _ = scripted_runtime(
        make_agent(callbacks=Callbacks(before_agent=[before_agent])), [[{"text": "a"}]]
    )

    await run_turn(runtime, "go")

    assert seen == ["test-agent"]


def test_agent_rejects_tools_that_do_not_match_the_definition():
    definition = AgentDefinition(
        name="a",
        version="1",
        model="m",
        instructions="i",
        tools=[{"name": "missing", "function": "tools:missing"}],
    )
    with pytest.raises(ValueError, match="declares tools"):
        Agent(definition=definition, tools=[add])


async def test_runtime_creates_one_invocation_per_agent_run():
    seen = []
    callbacks = Callbacks(before_agent=[lambda ctx: seen.append(ctx.invocation)])
    runtime, model = scripted_runtime(
        make_agent(add, callbacks=callbacks), [[call("add", a=1, b=2)], [{"text": "3"}], [{"text": "again"}]]
    )

    first = await run_turn(runtime, "go")
    second = await run_turn(runtime, "go again")

    invocation = seen[0]
    assert (invocation.type, invocation.name, invocation.version, invocation.parent_id) == (
        "agent",
        "test-agent",
        "1.0.0",
        None,
    )
    # Every event of the turn, and every gateway call, carries that invocation.
    assert {(e.invocation_id, e.invocation_type) for e in first} == {(invocation.id, "agent")}
    assert {(r.context.invocation_id, r.context.invocation_type) for r in model.requests[:2]} == {
        (invocation.id, "agent")
    }
    # A new message is a new invocation.
    assert second[0].invocation_id not in (None, invocation.id)


async def test_event_before_any_invocation_has_no_invocation_id():
    def refuse(ctx):
        raise PermissionError("not today")

    runtime, model = scripted_runtime(make_agent(callbacks=Callbacks(before_runtime=[refuse])), [])

    events = await run_turn(runtime, "go")

    assert [(e.type, e.invocation_id, e.invocation_type) for e in events] == [("error", None, None)]
    assert model.requests == []


async def test_turn_for_a_conversation_that_was_never_created_is_an_error():
    from ap_sdk.testing import make_envelope

    runtime, model = scripted_runtime(make_agent(), [[{"text": "a"}]])

    events = [e async for e in runtime.run_turn(make_envelope("hi", conversation_id="never-created"))]

    assert [e.type for e in events] == ["error"]
    assert "UnknownConversationError" in events[0].data["message"]
    assert model.requests == []
