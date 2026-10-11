"""Model gateway: the only path from the runtime to a model provider.

Pass-through for now. Authorization, guardrails and budgets (docs/design/05) attach here later;
callers do not change when they do.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Protocol

from opentelemetry import trace

from ap_contracts import ModelDone, ModelEvent, ModelRequest

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("ap_gateway")


class ModelAdapter(Protocol):
    """One provider. Translates the internal format to the provider's API and back."""

    provider: str

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Yield the model's events. The last event must be a ModelDone."""
        ...


class UnknownModelError(LookupError):
    pass


class ModelResolver(Protocol):
    def resolve(self, name: str) -> tuple[ModelAdapter, str]:
        """The adapter for a model name, and the provider's own id for that model."""
        ...


class ModelGateway:
    def __init__(
        self,
        registry: ModelResolver | None = None,
        *,
        adapters: dict[str, ModelAdapter] | None = None,
        default: ModelAdapter | None = None,
    ):
        """Models are looked up in `registry`. Without one, `adapters` maps a model name to its
        adapter and `default` serves any other model, which is how tests plug in a fake model."""
        self._registry = registry
        self._adapters = dict(adapters or {})
        self._default = default

    def _resolve(self, model: str) -> tuple[ModelAdapter, str]:
        if self._registry is not None:
            return self._registry.resolve(model)
        adapter = self._adapters.get(model, self._default)
        if adapter is None:
            raise UnknownModelError(f"No adapter registered for model '{model}'")
        return adapter, model

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        adapter, provider_model = self._resolve(request.model)
        if provider_model != request.model:
            request = request.model_copy(update={"model": provider_model})
        ctx = request.context
        # Span and attribute names follow the OpenTelemetry GenAI semantic conventions.
        with tracer.start_as_current_span(f"chat {request.model}", kind=trace.SpanKind.CLIENT) as span:
            span.set_attributes(
                {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": adapter.provider,
                    "gen_ai.request.model": request.model,
                    "gen_ai.request.max_tokens": request.max_tokens,
                    "gen_ai.conversation.id": ctx.conversation_id,
                    "gen_ai.agent.name": ctx.agent,
                    "agent_platform.invocation.id": ctx.invocation_id,
                    "agent_platform.message.id": ctx.message_id,
                    "agent_platform.tenant": ctx.tenant,
                }
            )
            done: ModelDone | None = None
            async for event in adapter.stream(request):
                if isinstance(event, ModelDone):
                    done = event
                yield event
            if done is None:
                raise RuntimeError(f"Adapter '{adapter.provider}' ended its stream without a ModelDone")
            span.set_attribute("gen_ai.response.finish_reasons", [done.stop_reason])
            if done.usage is not None:
                span.set_attribute("gen_ai.usage.input_tokens", done.usage.input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", done.usage.output_tokens)
            logger.info(
                "model call",
                extra={
                    "conversation_id": ctx.conversation_id,
                    "invocation_id": ctx.invocation_id,
                    "model": request.model,
                    "stop_reason": done.stop_reason,
                },
            )
