import pytest
from google.genai import types

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
    ToolResultBlock,
    ToolSpec,
)
from ap_gateway.gemini_adapter import LOCAL_ID_PREFIX, GeminiAdapter, to_gemini_contents

CONTEXT = CallContext(
    tenant="t",
    user_id="u",
    conversation_id="c1",
    message_id="m1",
    invocation_id="i1",
    invocation_type="agent",
    agent="a",
    agent_version="1",
)
WEATHER = ToolSpec(
    name="get_weather",
    description="Current weather.",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
)


class FakeGemini:
    """Stands in for genai.Client: replays chunks and records the call."""

    def __init__(self, chunks: list[types.GenerateContentResponse]):
        self._chunks = chunks
        self.calls: list[dict] = []
        self.aio = self
        self.models = self

    async def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)

        async def chunks():
            for chunk in self._chunks:
                yield chunk

        return chunks()


def chunk(*parts: types.Part, finish: str | None = None, usage: tuple[int, int] | None = None):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=list(parts)),
                finish_reason=types.FinishReason[finish] if finish else None,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=usage[0], candidates_token_count=usage[1]
        )
        if usage
        else None,
    )


def request(messages: list[Message], tools: list[ToolSpec] | None = None) -> ModelRequest:
    return ModelRequest(
        context=CONTEXT,
        model="gemini-test",
        system="Be brief.",
        messages=messages,
        tools=tools or [],
        max_tokens=500,
    )


async def collect(adapter: GeminiAdapter, req: ModelRequest):
    return [event async for event in adapter.stream(req)]


USER = Message(role="user", content=[TextBlock(text="Weather in Pune?")])


async def test_text_reply_is_streamed_and_the_request_is_translated():
    client = FakeGemini(
        [chunk(types.Part(text="It is ")), chunk(types.Part(text="sunny."), finish="STOP", usage=(12, 5))]
    )

    events = await collect(GeminiAdapter(client), request([USER], [WEATHER]))

    assert events[:2] == [TextDelta(text="It is "), TextDelta(text="sunny.")]
    assert isinstance(events[2], ProviderBlock) and events[2].provider == "gemini"
    assert events[3] == ModelDone(stop_reason="end_turn", usage={"input_tokens": 12, "output_tokens": 5})
    call = client.calls[0]
    assert call["model"] == "gemini-test"
    assert call["contents"] == [types.Content(role="user", parts=[types.Part(text="Weather in Pune?")])]
    assert call["config"].system_instruction == "Be brief."
    assert call["config"].max_output_tokens == 500
    declaration = call["config"].tools[0].function_declarations[0]
    assert (declaration.name, declaration.parameters_json_schema) == ("get_weather", WEATHER.input_schema)


async def test_function_call_becomes_a_tool_call_and_thoughts_are_not_shown():
    client = FakeGemini(
        [
            chunk(types.Part(text="thinking about it", thought=True)),
            chunk(
                types.Part(
                    function_call=types.FunctionCall(name="get_weather", args={"city": "Pune"}),
                    thought_signature=b"sig-1",
                ),
                finish="STOP",
            ),
        ]
    )

    events = await collect(GeminiAdapter(client), request([USER], [WEATHER]))

    call = events[0]
    assert isinstance(call, ToolCall) and (call.name, call.arguments) == ("get_weather", {"city": "Pune"})
    assert call.id.startswith(LOCAL_ID_PREFIX)  # Gemini gave no id
    assert not any(isinstance(e, TextDelta) for e in events)
    assert events[-1].stop_reason == "tool_use"


async def test_native_id_is_kept_when_gemini_gives_one():
    client = FakeGemini(
        [
            chunk(
                types.Part(function_call=types.FunctionCall(id="fc_9", name="get_weather", args={})),
                finish="STOP",
            )
        ]
    )

    events = await collect(GeminiAdapter(client), request([USER], [WEATHER]))

    assert events[0].id == "fc_9"


@pytest.mark.parametrize(
    ("finish", "expected"),
    [("MAX_TOKENS", "max_tokens"), ("SAFETY", "refusal"), ("OTHER", "other"), (None, "other")],
)
async def test_finish_reasons_map_to_stop_reasons(finish, expected):
    client = FakeGemini([chunk(types.Part(text="x"), finish=finish)])

    events = await collect(GeminiAdapter(client), request([USER]))

    assert events[-1].stop_reason == expected


async def test_truncated_function_call_is_not_reported_as_tool_use():
    client = FakeGemini(
        [
            chunk(
                types.Part(function_call=types.FunctionCall(name="get_weather", args={})), finish="MAX_TOKENS"
            )
        ]
    )

    events = await collect(GeminiAdapter(client), request([USER], [WEATHER]))

    assert events[-1].stop_reason == "max_tokens"


async def test_a_gemini_turn_is_replayed_exactly_with_its_thought_signature():
    """What the adapter emits for a turn, stored by the runtime, goes back to Gemini unchanged."""
    native_part = types.Part(
        function_call=types.FunctionCall(name="get_weather", args={"city": "Pune"}),
        thought_signature=b"\x00sig\xff",
    )
    first = await collect(
        GeminiAdapter(FakeGemini([chunk(native_part, finish="STOP")])), request([USER], [WEATHER])
    )
    call, provider_block = first[0], first[1]
    assistant = Message(
        role="assistant",
        content=[ToolCallBlock(id=call.id, name=call.name, arguments=call.arguments), provider_block],
    )
    result = Message(role="user", content=[ToolResultBlock(tool_call_id=call.id, content="29C")])

    contents = to_gemini_contents([USER, assistant, result])

    assert contents[1] == types.Content(role="model", parts=[native_part])
    response = contents[2].parts[0].function_response
    assert (response.name, response.response, response.id) == ("get_weather", {"result": "29C"}, None)


def test_history_from_another_provider_is_rebuilt_from_the_neutral_blocks():
    messages = [
        USER,
        Message(
            role="assistant",
            content=[
                ProviderBlock(
                    provider="anthropic", data={"type": "thinking", "thinking": "", "signature": "s"}
                ),
                TextBlock(text="Checking."),
                ToolCallBlock(id="toolu_1", name="get_weather", arguments={"city": "Pune"}),
            ],
        ),
        Message(
            role="user", content=[ToolResultBlock(tool_call_id="toolu_1", content="no data", is_error=True)]
        ),
    ]

    contents = to_gemini_contents(messages)

    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts == [
        types.Part(text="Checking."),
        types.Part(function_call=types.FunctionCall(id="toolu_1", name="get_weather", args={"city": "Pune"})),
    ]
    response = contents[2].parts[0].function_response
    assert (response.id, response.name, response.response) == ("toolu_1", "get_weather", {"error": "no data"})
