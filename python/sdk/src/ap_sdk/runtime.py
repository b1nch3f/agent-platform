"""Runtime kernel: runs one turn of an agent (docs/design/04).

The runtime makes no decisions about content. It routes the turn, keeps the working set,
writes every message through to the store, and sends every model and tool call to the gateway.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace

from ap_contracts import (
    CallContext,
    ContentBlock,
    Message,
    ModelDone,
    ModelRequest,
    ProviderBlock,
    TextBlock,
    TextDelta,
    ToolCall,
    ToolCallBlock,
    ToolCallRequest,
    ToolCallResult,
    ToolResultBlock,
    TurnEnvelope,
)
from ap_gateway import ModelGateway, ToolGateway
from ap_sdk.agent import Agent
from ap_sdk.callbacks import Callbacks, invoke
from ap_sdk.store import ConversationStore

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("ap_sdk")


@dataclass
class RuntimeEvent:
    """What the runtime emits during a turn. The transport turns it into an OutboundEvent."""

    type: str  # token | tool_status | turn_done | error
    data: dict[str, Any]
    invocation_id: str | None = None


@dataclass
class TurnContext:
    """State of one turn, passed to every callback."""

    envelope: TurnEnvelope
    agent: Agent | None = None  # set by the turn router
    working_set: list[Message] = field(default_factory=list)  # in memory, for this turn only
    stop_reason: str | None = None
    state: dict[str, Any] = field(default_factory=dict)  # free for callbacks to use

    @property
    def conversation_id(self) -> str:
        return self.envelope.conversation_id

    def call_context(self) -> CallContext:
        return CallContext(
            tenant=self.envelope.identity.tenant,
            user_id=self.envelope.identity.user_id,
            conversation_id=self.envelope.conversation_id,
            message_id=self.envelope.message_id,
            invocation_id=self.envelope.invocation_id,
            agent=self.agent.name,
            agent_version=self.agent.version,
        )


class Runtime:
    def __init__(
        self,
        agent: Agent,
        model_gateway: ModelGateway,
        tool_gateway: ToolGateway,
        store: ConversationStore,
    ):
        self.root_agent = agent
        self.model_gateway = model_gateway
        self.tool_gateway = tool_gateway
        self.store = store
        for tool in agent.tools:
            tool_gateway.register(tool.name, tool.handler)
        # Platform callbacks run before the agent's own at every hook.
        self._platform = Callbacks(
            before_runtime=[self._route_turn],
            before_model=[self._assemble_context],
            after_runtime=[self._turn_completed],
        )

    # --- platform callbacks -------------------------------------------------

    def _route_turn(self, ctx: TurnContext) -> None:
        """Turn router. With no workflows yet, there is no waiting step to offer the message to,
        so every message goes to the root agent."""
        ctx.agent = self.root_agent

    def _assemble_context(self, ctx: TurnContext, request: ModelRequest) -> ModelRequest:
        """Context assembly: the model sees the working set."""
        return request.model_copy(update={"messages": list(ctx.working_set)})

    def _turn_completed(self, ctx: TurnContext) -> None:
        logger.info(
            "turn.completed",
            extra={
                "conversation_id": ctx.conversation_id,
                "message_id": ctx.envelope.message_id,
                "invocation_id": ctx.envelope.invocation_id,
                "stop_reason": ctx.stop_reason,
            },
        )

    # --- turn ---------------------------------------------------------------

    async def run_turn(self, envelope: TurnEnvelope) -> AsyncIterator[RuntimeEvent]:
        """Run one turn. Yields events as they happen; the last is `turn_done` or `error`."""
        ctx = TurnContext(envelope=envelope)
        callbacks = self._platform
        try:
            if envelope.kind != "message":
                raise NotImplementedError(f"Turn kind '{envelope.kind}' is not supported yet")
            text = envelope.payload.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError("A message turn needs a non-empty payload.text")

            await self._platform.run("before_runtime", ctx)
            callbacks = self._platform.then(ctx.agent.callbacks)
            await ctx.agent.callbacks.run("before_runtime", ctx)

            # Load once, then append through: the store first, the working set second.
            ctx.working_set = await self.store.load(ctx.conversation_id)
            await self._append(ctx, Message(role="user", content=[TextBlock(text=text)]), envelope.message_id)

            async for event in self._invoke_agent(ctx, callbacks):
                yield event

            await callbacks.run("after_runtime", ctx)
            yield RuntimeEvent("turn_done", {"stop_reason": ctx.stop_reason}, envelope.invocation_id)
        except Exception as exc:
            logger.exception(
                "turn failed",
                extra={"conversation_id": ctx.conversation_id, "invocation_id": envelope.invocation_id},
            )
            await callbacks.run("on_runtime_error", ctx, exc)
            yield RuntimeEvent("error", {"message": f"{type(exc).__name__}: {exc}"}, envelope.invocation_id)

    async def _append(self, ctx: TurnContext, message: Message, message_id: str | None = None) -> None:
        await self.store.append(ctx.conversation_id, message, message_id=message_id)
        ctx.working_set.append(message)

    async def _invoke_agent(self, ctx: TurnContext, callbacks: Callbacks) -> AsyncIterator[RuntimeEvent]:
        agent = ctx.agent
        with tracer.start_as_current_span(f"invoke_agent {agent.name}") as span:
            span.set_attributes(
                {
                    "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.agent.name": agent.name,
                    "gen_ai.conversation.id": ctx.conversation_id,
                    "agent_platform.agent.version": agent.version,
                    "agent_platform.invocation.id": ctx.envelope.invocation_id,
                    "agent_platform.message.id": ctx.envelope.message_id,
                    "agent_platform.session.id": ctx.envelope.session_id,
                    "agent_platform.tenant": ctx.envelope.identity.tenant,
                }
            )
            try:
                await callbacks.run("before_agent", ctx)
                async for event in self._agent_loop(ctx, callbacks):
                    yield event
                await callbacks.run("after_agent", ctx)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                await callbacks.run("on_agent_error", ctx, exc)
                raise

    async def _agent_loop(self, ctx: TurnContext, callbacks: Callbacks) -> AsyncIterator[RuntimeEvent]:
        definition = ctx.agent.definition
        invocation_id = ctx.envelope.invocation_id
        tool_specs = [tool.spec() for tool in ctx.agent.tools]

        for _ in range(definition.max_steps):
            # Model call
            request = ModelRequest(
                context=ctx.call_context(),
                model=definition.model,
                system=definition.instructions,
                messages=[],
                tools=tool_specs,
                max_tokens=definition.max_tokens,
            )
            for callback in callbacks.before_model:  # each sees the previous one's request
                request = await invoke(callback, ctx, request) or request

            blocks: list[ContentBlock] = []
            tool_calls: list[ToolCall] = []
            done: ModelDone | None = None
            try:
                async for event in self.model_gateway.stream(request):
                    if isinstance(event, TextDelta):
                        if blocks and isinstance(blocks[-1], TextBlock):
                            blocks[-1].text += event.text
                        else:
                            blocks.append(TextBlock(text=event.text))
                        yield RuntimeEvent("token", {"text": event.text}, invocation_id)
                    elif isinstance(event, ToolCall):
                        tool_calls.append(event)
                        blocks.append(ToolCallBlock(id=event.id, name=event.name, arguments=event.arguments))
                    elif isinstance(event, ProviderBlock):
                        blocks.append(event)
                    elif isinstance(event, ModelDone):
                        done = event
            except Exception as exc:
                await callbacks.run("on_model_error", ctx, exc)
                raise

            assistant = Message(role="assistant", content=blocks)
            await callbacks.run("after_model", ctx, assistant, done)
            if blocks:
                await self._append(ctx, assistant)
            ctx.stop_reason = done.stop_reason

            if not tool_calls:
                return

            # Tool calls. Every call gets a result, so the history stays valid for the next model call.
            if done.stop_reason == "tool_use":
                for call in tool_calls:
                    yield RuntimeEvent(
                        "tool_status",
                        {"tool_call_id": call.id, "tool": call.name, "status": "started"},
                        invocation_id,
                    )
                results = await asyncio.gather(
                    *(self._call_tool(ctx, callbacks, call) for call in tool_calls)
                )
                for call, result in zip(tool_calls, results):
                    status = "failed" if result.is_error else "completed"
                    yield RuntimeEvent(
                        "tool_status",
                        {"tool_call_id": call.id, "tool": call.name, "status": status},
                        invocation_id,
                    )
            else:
                # The output was cut off (max_tokens) or declined (refusal): the arguments cannot be trusted.
                results = [
                    ToolCallResult(
                        tool_call_id=call.id,
                        content=f"Not run: the model's output ended early ({done.stop_reason}).",
                        is_error=True,
                    )
                    for call in tool_calls
                ]

            # All results of one model call go back in a single message.
            await self._append(
                ctx,
                Message(
                    role="user",
                    content=[
                        ToolResultBlock(tool_call_id=r.tool_call_id, content=r.content, is_error=r.is_error)
                        for r in results
                    ],
                ),
            )
            if done.stop_reason == "refusal":
                return

        ctx.stop_reason = "max_steps"

    async def _call_tool(self, ctx: TurnContext, callbacks: Callbacks, call: ToolCall) -> ToolCallResult:
        request = ToolCallRequest(
            context=ctx.call_context(), tool_call_id=call.id, name=call.name, arguments=call.arguments
        )
        try:
            result: ToolCallResult | None = None
            for callback in callbacks.before_tool:
                result = (
                    await invoke(callback, ctx, request) or result
                )  # an answer means the tool is not called
            if result is None:
                result = await self.tool_gateway.call(request)
            for callback in callbacks.after_tool:  # each sees the previous one's result
                result = await invoke(callback, ctx, request, result) or result
            return result
        except Exception as exc:  # noqa: BLE001
            # A failing callback fails this tool call, not the turn.
            await callbacks.run("on_tool_error", ctx, request, exc)
            return ToolCallResult(tool_call_id=call.id, content=f"{type(exc).__name__}: {exc}", is_error=True)
