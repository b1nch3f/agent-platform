"""The transport's core, independent of any network protocol (docs/design/03).

Issues conversation IDs, checks that a message belongs to a conversation it issued and is not a
repeat, normalizes each message into a TurnEnvelope, and stamps the runtime's events with the
conversation's cursor for delivery. It makes no decisions.
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
from ap_sdk import Runtime, RuntimeEvent

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

    def stamp(self, envelope: TurnEnvelope, event: RuntimeEvent, data: dict) -> OutboundEvent:
        self.cursor += 1
        outbound = OutboundEvent(
            conversation_id=envelope.conversation_id,
            message_id=envelope.message_id,
            invocation_id=event.invocation_id,
            invocation_type=event.invocation_type,
            cursor=self.cursor,
            type=event.type,
            data=data,
        )
        self.buffer.append(outbound)
        return outbound


def current_trace_id() -> str | None:
    span_context = trace.get_current_span().get_span_context()
    return format(span_context.trace_id, "032x") if span_context.is_valid else None


class UnknownConversation(Exception):
    """The conversation was not issued by this transport, or belongs to another caller."""


class DuplicateMessage(Exception):
    """This message_id was already sent in the conversation."""


class Dispatcher:
    def __init__(self, runtime: Runtime, *, protocol: Protocol, identity: CallIdentity = DEV_IDENTITY):
        self.runtime = runtime
        self._protocol = protocol
        self._identity = identity
        self._channels: dict[str, ConversationChannel] = {}

    def _channel_for(self, conversation_id: str) -> ConversationChannel:
        return self._channels.setdefault(conversation_id, ConversationChannel())

    @property
    def _owner(self) -> dict[str, str]:
        return {"tenant": self._identity.tenant, "user_id": self._identity.user_id}

    async def start(self, message_id: str, text: str) -> tuple[str, AsyncIterator[OutboundEvent]]:
        """Start a conversation with its first message. Returns the new conversation's ID and
        the turn's events. Conversation IDs are only ever created here."""
        conversation_id = uuid.uuid4().hex
        await self.runtime.store.create(conversation_id, **self._owner)
        return conversation_id, self._run(conversation_id, message_id, text)

    async def send(self, conversation_id: str, message_id: str, text: str) -> AsyncIterator[OutboundEvent]:
        """Continue a conversation. Raises UnknownConversation or DuplicateMessage before any
        event is produced, so a caller can reject the request outright."""
        await self.check_conversation(conversation_id)
        if await self.runtime.store.has_message(conversation_id, message_id):
            raise DuplicateMessage(message_id)
        return self._run(conversation_id, message_id, text)

    async def check_conversation(self, conversation_id: str) -> None:
        if not await self.runtime.store.exists(conversation_id, **self._owner):
            raise UnknownConversation(conversation_id)

    async def _run(self, conversation_id: str, message_id: str, text: str) -> AsyncIterator[OutboundEvent]:
        """Run one turn and yield its events, stamped with the conversation's cursor."""
        envelope = TurnEnvelope(
            message_id=message_id,
            conversation_id=conversation_id,
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
                yield channel.stamp(envelope, event, data)

    async def replay(self, conversation_id: str, after_cursor: int = 0) -> list[OutboundEvent]:
        """Buffered events after a cursor, for a client that lost its stream."""
        await self.check_conversation(conversation_id)
        return [e for e in self._channel_for(conversation_id).buffer if e.cursor > after_cursor]
