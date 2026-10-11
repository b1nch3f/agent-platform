"""The transport's core, independent of any network protocol (docs/design/03).

Owns the identifiers that tie connections, conversations and runs together, normalizes each
message into a TurnEnvelope, and stamps the runtime's events for delivery. It makes no decisions.
The HTTP app and the in-process client are both thin layers over it.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import AsyncIterator
from typing import Literal

from opentelemetry import trace

from ap_contracts import CallIdentity, OutboundEvent, TurnEnvelope
from ap_sdk import Runtime

# MVP: a fixed identity. Session binding (token, tenant, entitlement) replaces it with governance.
DEV_IDENTITY = CallIdentity(tenant="dev", user_id="dev-user")
REPLAY_BUFFER_SIZE = 1000

Protocol = Literal["websocket", "http_sse", "a2a", "kafka", "local"]


class ConversationChannel:
    """Per-conversation delivery state: the cursor, a short replay buffer, and a lock that
    keeps turns of one conversation from interleaving."""

    def __init__(self) -> None:
        self.cursor = 0
        self.buffer: deque[OutboundEvent] = deque(maxlen=REPLAY_BUFFER_SIZE)
        self.lock = asyncio.Lock()

    def stamp(
        self, envelope: TurnEnvelope, type: str, data: dict, invocation_id: str | None
    ) -> OutboundEvent:
        self.cursor += 1
        event = OutboundEvent(
            conversation_id=envelope.conversation_id,
            message_id=envelope.message_id,
            invocation_id=invocation_id,
            cursor=self.cursor,
            type=type,
            data=data,
        )
        self.buffer.append(event)
        return event


def current_trace_id() -> str | None:
    span_context = trace.get_current_span().get_span_context()
    return format(span_context.trace_id, "032x") if span_context.is_valid else None


class Dispatcher:
    def __init__(self, runtime: Runtime, *, protocol: Protocol, identity: CallIdentity = DEV_IDENTITY):
        self.runtime = runtime
        self._protocol = protocol
        self._identity = identity
        self._channels: dict[str, ConversationChannel] = {}

    def _channel_for(self, conversation_id: str) -> ConversationChannel:
        return self._channels.setdefault(conversation_id, ConversationChannel())

    @staticmethod
    def new_conversation_id() -> str:
        return uuid.uuid4().hex

    async def is_duplicate(self, conversation_id: str, message_id: str) -> bool:
        """True if this message_id was already sent in the conversation."""
        return await self.runtime.store.has_message(conversation_id, message_id)

    async def send(self, conversation_id: str, message_id: str, text: str) -> AsyncIterator[OutboundEvent]:
        """Run one turn for a user message and yield its events, stamped with the conversation's cursor."""
        envelope = TurnEnvelope(
            message_id=message_id,
            conversation_id=conversation_id,
            session_id=uuid.uuid4().hex,  # one per connection
            invocation_id=uuid.uuid4().hex,  # the top-level run for this message
            protocol=self._protocol,
            identity=self._identity,
            kind="message",
            payload={"text": text},
        )
        channel = self._channel_for(conversation_id)
        trace_id = current_trace_id()
        async with channel.lock:
            async for event in self.runtime.run_turn(envelope):
                data = dict(event.data)
                if event.type == "turn_done" and trace_id:
                    data["trace_id"] = trace_id
                yield channel.stamp(envelope, event.type, data, event.invocation_id)

    def replay(self, conversation_id: str, after_cursor: int = 0) -> list[OutboundEvent]:
        """Buffered events after a cursor, for a client that lost its stream."""
        return [e for e in self._channel_for(conversation_id).buffer if e.cursor > after_cursor]
