import pytest

from ap_contracts import (
    CallContext,
    Message,
    ModelDone,
    ModelRequest,
    ProviderBlock,
    TextBlock,
    TextDelta,
    ToolCall,
    ToolCallBlock,
    ToolCallRequest,
    ToolResultBlock,
)
from ap_gateway import ModelGateway, ScriptedModel, ScriptExhaustedError, ToolGateway, UnknownModelError
from ap_gateway.anthropic_adapter import to_anthropic_messages

CONTEXT = CallContext(
    tenant="t",
    user_id="u",
    conversation_id="c1",
    message_id="m1",
    invocation_id="i1",
    agent="a",
    agent_version="1",
)


def request(model: str = "fake") -> ModelRequest:
    return ModelRequest(context=CONTEXT, model=model, system="s", messages=[], max_tokens=100)


async def collect(stream):
    return [event async for event in stream]


async def test_scripted_model_replays_turns_and_records_requests():
    model = ScriptedModel(
        [[{"text": "a"}, {"tool_call": {"name": "t", "arguments": {"x": 1}}}], [{"text": "b"}]]
    )

    first = await collect(model.stream(request()))
    second = await collect(model.stream(request()))

    assert first == [
        TextDelta(text="a"),
        ToolCall(id="call_0_1", name="t", arguments={"x": 1}),
        ModelDone(stop_reason="tool_use", usage={"input_tokens": 0, "output_tokens": 0}),
    ]
    assert second[-1].stop_reason == "end_turn"
    assert len(model.requests) == 2


async def test_scripted_model_fails_when_called_more_than_scripted():
    model = ScriptedModel([])
    with pytest.raises(ScriptExhaustedError):
        await collect(model.stream(request()))


async def test_model_gateway_routes_by_model_name():
    named, default = ScriptedModel([[{"text": "named"}]]), ScriptedModel([[{"text": "default"}]])
    gateway = ModelGateway(adapters={"special": named}, default=default)

    assert (await collect(gateway.stream(request("special"))))[0].text == "named"
    assert (await collect(gateway.stream(request("anything"))))[0].text == "default"


async def test_model_gateway_rejects_unknown_model():
    with pytest.raises(UnknownModelError):
        await collect(ModelGateway().stream(request()))


async def test_model_gateway_requires_stream_to_end_with_done():
    class NoDone:
        provider = "broken"

        async def stream(self, request):
            yield TextDelta(text="x")

    with pytest.raises(RuntimeError, match="without a ModelDone"):
        await collect(ModelGateway(default=NoDone()).stream(request()))


def tool_request(name: str, arguments: dict) -> ToolCallRequest:
    return ToolCallRequest(context=CONTEXT, tool_call_id="tc1", name=name, arguments=arguments)


async def test_tool_gateway_calls_the_registered_handler():
    gateway = ToolGateway()

    async def echo(arguments):
        return f"got {arguments['x']}"

    gateway.register("echo", echo)
    result = await gateway.call(tool_request("echo", {"x": 7}))

    assert (result.tool_call_id, result.content, result.is_error) == ("tc1", "got 7", False)


async def test_tool_gateway_turns_failures_into_error_results():
    gateway = ToolGateway()

    async def boom(arguments):
        raise LookupError("nope")

    gateway.register("boom", boom)

    failed = await gateway.call(tool_request("boom", {}))
    unknown = await gateway.call(tool_request("missing", {}))

    assert (failed.is_error, failed.content) == (True, "LookupError: nope")
    assert (unknown.is_error, unknown.content) == (True, "Unknown tool 'missing'")


def test_anthropic_message_translation_keeps_order_and_native_blocks():
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    messages = [
        Message(role="user", content=[TextBlock(text="hi")]),
        Message(
            role="assistant",
            content=[
                ProviderBlock(provider="anthropic", data=thinking),
                ProviderBlock(provider="other", data={"type": "x"}),
                TextBlock(text="checking"),
                ToolCallBlock(id="tc1", name="get_weather", arguments={"city": "Pune"}),
            ],
        ),
        Message(role="user", content=[ToolResultBlock(tool_call_id="tc1", content="29", is_error=False)]),
    ]

    assert to_anthropic_messages(messages) == [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {
            "role": "assistant",
            "content": [
                thinking,
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "tc1", "name": "get_weather", "input": {"city": "Pune"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tc1", "content": "29", "is_error": False}],
        },
    ]
