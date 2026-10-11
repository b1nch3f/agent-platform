"""Gemini through Google's Gen AI SDK."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from google import genai
from google.genai import types

from ap_contracts import (
    Message,
    ModelDone,
    ModelEvent,
    ModelRequest,
    ProviderBlock,
    TextBlock,
    TextDelta,
    ToolCall,
    ToolCallBlock,
    ToolResultBlock,
    Usage,
)

PROVIDER = "gemini"
# Gemini does not always give a function call an id; ours are marked so they are not sent back as if native.
LOCAL_ID_PREFIX = "gemini_local_"
_REFUSAL_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}


def _native_content(message: Message) -> types.Content | None:
    """The model turn exactly as Gemini produced it, if this message came from Gemini."""
    for block in message.content:
        if isinstance(block, ProviderBlock) and block.provider == PROVIDER and "content" in block.data:
            return types.Content.model_validate_json(json.dumps(block.data["content"]))
    return None


def to_gemini_contents(messages: list[Message]) -> list[types.Content]:
    """Internal messages to Gemini contents.

    A model turn that came from Gemini is sent back as Gemini produced it, because its parts carry
    thought signatures that must be returned unchanged. Other turns are built from the neutral blocks.
    """
    tool_names: dict[str, str] = {}
    contents = []
    for message in messages:
        for block in message.content:
            if isinstance(block, ToolCallBlock):
                tool_names[block.id] = block.name

        if message.role == "assistant" and (native := _native_content(message)) is not None:
            contents.append(native)
            continue

        parts = []
        for block in message.content:
            if isinstance(block, TextBlock):
                parts.append(types.Part(text=block.text))
            elif isinstance(block, ToolCallBlock):
                call_id = None if block.id.startswith(LOCAL_ID_PREFIX) else block.id
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(id=call_id, name=block.name, args=block.arguments)
                    )
                )
            elif isinstance(block, ToolResultBlock):
                call_id = None if block.tool_call_id.startswith(LOCAL_ID_PREFIX) else block.tool_call_id
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=call_id,
                            name=tool_names.get(block.tool_call_id, "unknown"),
                            response={"error" if block.is_error else "result": block.content},
                        )
                    )
                )
            # Another provider's native blocks mean nothing to this one and are left out.
        if parts:
            contents.append(
                types.Content(role="model" if message.role == "assistant" else "user", parts=parts)
            )
    return contents


def _stop_reason(finish_reason: str | None, has_tool_call: bool) -> str:
    if finish_reason == "MAX_TOKENS":
        return "max_tokens"
    if finish_reason in _REFUSAL_REASONS:
        return "refusal"
    if has_tool_call:
        return "tool_use"
    return "end_turn" if finish_reason == "STOP" else "other"


class GeminiAdapter:
    provider = PROVIDER

    def __init__(self, client: genai.Client | None = None):
        # MVP: the client reads GEMINI_API_KEY from the environment. This becomes a vault
        # reference resolved by the credential service (docs/adr/0002).
        # A briefly overloaded model answers 503, so server errors are retried. 429 is not:
        # a retry spends more of the quota that was just exceeded.
        self._client = client or genai.Client(
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=4, http_status_codes=[500, 502, 503, 504])
            )
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        config: dict[str, Any] = {
            "system_instruction": request.system,
            "max_output_tokens": request.max_tokens,
        }
        if request.tools:
            config["tools"] = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool.name,
                            description=tool.description,
                            parameters_json_schema=tool.input_schema,
                        )
                        for tool in request.tools
                    ]
                )
            ]

        parts: list[types.Part] = []
        finish_reason: str | None = None
        has_tool_call = False
        usage = Usage()
        stream = await self._client.aio.models.generate_content_stream(
            model=request.model,
            contents=to_gemini_contents(request.messages),
            config=types.GenerateContentConfig(**config),
        )
        async for chunk in stream:
            if chunk.usage_metadata is not None:
                usage = Usage(
                    input_tokens=chunk.usage_metadata.prompt_token_count or 0,
                    output_tokens=(chunk.usage_metadata.candidates_token_count or 0)
                    + (chunk.usage_metadata.thoughts_token_count or 0),
                )
            if not chunk.candidates:
                continue
            candidate = chunk.candidates[0]
            if candidate.finish_reason is not None:
                finish_reason = candidate.finish_reason.name
            for part in (candidate.content.parts if candidate.content else None) or []:
                parts.append(part)
                if part.function_call is not None:
                    has_tool_call = True
                    call = part.function_call
                    yield ToolCall(
                        id=call.id or f"{LOCAL_ID_PREFIX}{uuid.uuid4().hex}",
                        name=call.name,
                        arguments=dict(call.args or {}),
                    )
                elif part.text and not part.thought:
                    yield TextDelta(text=part.text)

        if parts:
            # The turn as Gemini produced it, replayed as-is on the next call (see to_gemini_contents).
            native = types.Content(role="model", parts=parts)
            yield ProviderBlock(
                provider=PROVIDER, data={"content": json.loads(native.model_dump_json(exclude_none=True))}
            )
        yield ModelDone(stop_reason=_stop_reason(finish_reason, has_tool_call), usage=usage)
