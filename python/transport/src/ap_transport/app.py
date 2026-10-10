"""HTTP + SSE transport (docs/design/03).

Owns the identifiers that tie connections, conversations and runs together, normalizes each
request into a TurnEnvelope, and delivers the runtime's events. It makes no decisions.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from collections.abc import AsyncIterator

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field

from ap_contracts import CallIdentity, OutboundEvent, TurnEnvelope
from ap_sdk import Runtime

# MVP: a fixed identity. Session binding (token, tenant, entitlement) replaces it with governance.
DEV_IDENTITY = CallIdentity(tenant="dev", user_id="dev-user")
REPLAY_BUFFER_SIZE = 1000


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(min_length=1, description="Client-generated; a repeat is rejected with 409.")
    text: str = Field(min_length=1)


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


def to_sse(event: OutboundEvent) -> str:
    """One Server-Sent Event. The cursor is the SSE id, so a client resumes with Last-Event-ID."""
    return f"id: {event.cursor}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


def _current_trace_id() -> str | None:
    span_context = trace.get_current_span().get_span_context()
    return format(span_context.trace_id, "032x") if span_context.is_valid else None


def create_app(runtime: Runtime, identity: CallIdentity = DEV_IDENTITY) -> FastAPI:
    app = FastAPI(title="Agent Platform transport")
    channels: dict[str, ConversationChannel] = {}

    def channel_for(conversation_id: str) -> ConversationChannel:
        return channels.setdefault(conversation_id, ConversationChannel())

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "agent": runtime.root_agent.name, "version": runtime.root_agent.version}

    @app.post("/conversations", status_code=201)
    async def create_conversation() -> dict:
        return {"conversation_id": uuid.uuid4().hex}

    @app.post("/conversations/{conversation_id}/messages")
    async def post_message(conversation_id: str, body: MessageRequest) -> StreamingResponse:
        """Send one message and stream the turn's events back on the same connection."""
        if await runtime.store.has_message(conversation_id, body.message_id):
            raise HTTPException(status_code=409, detail=f"message_id '{body.message_id}' was already sent")

        envelope = TurnEnvelope(
            message_id=body.message_id,
            conversation_id=conversation_id,
            session_id=uuid.uuid4().hex,  # one per connection
            invocation_id=uuid.uuid4().hex,  # the top-level run for this message
            protocol="http_sse",
            identity=identity,
            kind="message",
            payload={"text": body.text},
        )
        channel = channel_for(conversation_id)
        trace_id = _current_trace_id()

        async def stream() -> AsyncIterator[str]:
            async with channel.lock:
                async for event in runtime.run_turn(envelope):
                    data = dict(event.data)
                    if event.type == "turn_done" and trace_id:
                        data["trace_id"] = trace_id
                    yield to_sse(channel.stamp(envelope, event.type, data, event.invocation_id))

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)

    @app.get("/conversations/{conversation_id}/events")
    async def replay_events(
        conversation_id: str, last_event_id: int = Header(default=0, alias="Last-Event-ID")
    ) -> StreamingResponse:
        """Replay buffered events after the client's last cursor, for a client that lost its stream."""
        missed = [e for e in channel_for(conversation_id).buffer if e.cursor > last_event_id]

        async def stream() -> AsyncIterator[str]:
            for event in missed:
                yield to_sse(event)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    return app
