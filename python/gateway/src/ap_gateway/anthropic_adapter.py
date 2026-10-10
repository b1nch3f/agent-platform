"""Claude through the Anthropic API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from anthropic import AsyncAnthropic

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

PROVIDER = "anthropic"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_STOP_REASONS = {"end_turn", "tool_use", "max_tokens", "refusal"}


def to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Internal messages to Anthropic message params. Provider blocks go back unchanged, in position."""
    result = []
    for message in messages:
        content: list[dict[str, Any]] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                content.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolCallBlock):
                content.append(
                    {"type": "tool_use", "id": block.id, "name": block.name, "input": block.arguments}
                )
            elif isinstance(block, ToolResultBlock):
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.tool_call_id,
                        "content": block.content,
                        "is_error": block.is_error,
                    }
                )
            elif isinstance(block, ProviderBlock) and block.provider == PROVIDER:
                content.append(block.data)
            # Another provider's native blocks mean nothing to this one and are left out.
        result.append({"role": message.role, "content": content})
    return result


class AnthropicAdapter:
    provider = PROVIDER

    def __init__(self, client: AsyncAnthropic | None = None, *, refusal_fallback: bool = True):
        """`refusal_fallback` lets the API re-run a request its safety classifiers declined on
        Anthropic's recommended fallback model, inside the same call."""
        # MVP: the client reads ANTHROPIC_API_KEY from the environment. This becomes a vault
        # reference resolved by the credential service (docs/adr/0002).
        self._client = client or AsyncAnthropic()
        self._refusal_fallback = refusal_fallback

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": to_anthropic_messages(request.messages),
        }
        if request.tools:
            params["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    # Inputs stream as generated and are not validated by the API;
                    # the Agent SDK validates them against the schema before running the tool.
                    "eager_input_streaming": True,
                }
                for tool in request.tools
            ]
        if self._refusal_fallback:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"

        async with self._client.beta.messages.stream(**params) as stream:
            async for event in stream:
                if event.type == "text":
                    yield TextDelta(text=event.text)
                elif event.type == "content_block_stop":
                    block = event.content_block
                    if block.type == "text":
                        continue
                    if block.type == "tool_use":
                        yield ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                    else:
                        # Thinking and any other native block: opaque to the runtime, replayed as-is.
                        yield ProviderBlock(
                            provider=PROVIDER,
                            data=block.model_dump(mode="json", exclude_none=True, by_alias=True),
                        )
            final = await stream.get_final_message()

        yield ModelDone(
            stop_reason=final.stop_reason if final.stop_reason in _STOP_REASONS else "other",
            usage=Usage(input_tokens=final.usage.input_tokens, output_tokens=final.usage.output_tokens),
        )
