"""Tool gateway: the only path from the runtime to a tool.

Pass-through for now. Authorization, guardrails, budgets and audit (docs/design/05) attach here later.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from opentelemetry import trace

from ap_contracts import ToolCallRequest, ToolCallResult

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("ap_gateway")

# Takes the model's arguments, returns the text shown to the model. Raises on failure.
ToolHandler = Callable[[dict[str, Any]], Awaitable[str]]


class ToolGateway:
    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, name: str, handler: ToolHandler) -> None:
        self._handlers[name] = handler

    async def call(self, request: ToolCallRequest) -> ToolCallResult:
        """Run one tool. A failing or unknown tool is an error result for the model, never an exception."""
        ctx = request.context
        with tracer.start_as_current_span(f"execute_tool {request.name}") as span:
            span.set_attributes(
                {
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": request.name,
                    "gen_ai.tool.call.id": request.tool_call_id,
                    "gen_ai.conversation.id": ctx.conversation_id,
                    "gen_ai.agent.name": ctx.agent,
                    "agent_platform.invocation.id": ctx.invocation_id,
                    "agent_platform.message.id": ctx.message_id,
                    "agent_platform.tenant": ctx.tenant,
                }
            )
            handler = self._handlers.get(request.name)
            if handler is None:
                content, is_error = f"Unknown tool '{request.name}'", True
            else:
                try:
                    content, is_error = await handler(request.arguments), False
                except Exception as exc:  # noqa: BLE001 - the model sees the failure and can react to it
                    span.record_exception(exc)
                    content, is_error = f"{type(exc).__name__}: {exc}", True
            if is_error:
                span.set_status(trace.StatusCode.ERROR, content)
            logger.info(
                "tool call",
                extra={
                    "conversation_id": ctx.conversation_id,
                    "invocation_id": ctx.invocation_id,
                    "tool": request.name,
                    "is_error": is_error,
                },
            )
            return ToolCallResult(tool_call_id=request.tool_call_id, content=content, is_error=is_error)
