"""HTTP + SSE transport: the network layer over the Dispatcher (docs/design/03).

POST /conversations                   start a conversation with its first message
POST /conversations/{id}/messages     continue it
GET  /conversations/{id}/events       replay events after Last-Event-ID
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ap_contracts import CallIdentity, OutboundEvent
from ap_sdk import Runtime
from ap_transport.dispatcher import (
    DEV_IDENTITY,
    Dispatcher,
    DuplicateMessage,
    UnknownConversation,
    current_trace_id,
)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(min_length=1, description="Client-generated; a repeat is rejected with 409.")
    text: str = Field(min_length=1)


def to_sse(event: OutboundEvent) -> str:
    """One Server-Sent Event. The cursor is the SSE id, so a client resumes with Last-Event-ID."""
    return f"id: {event.cursor}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


def _not_found(conversation_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=f"Unknown conversation '{conversation_id}'. Start one with POST /conversations "
        "and use the conversation_id it returns.",
    )


def create_app(runtime: Runtime, identity: CallIdentity = DEV_IDENTITY) -> FastAPI:
    app = FastAPI(title="Agent Platform transport")
    dispatcher = Dispatcher(runtime, protocol="http_sse", identity=identity)

    def sse_response(
        events: AsyncIterator[OutboundEvent], *, status_code: int = 200, headers: dict[str, str] | None = None
    ) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            async for event in events:
                yield to_sse(event)

        all_headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **(headers or {})}
        if trace_id := current_trace_id():
            all_headers["X-Trace-Id"] = trace_id
        return StreamingResponse(
            stream(), status_code=status_code, media_type="text/event-stream", headers=all_headers
        )

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "agent": runtime.root_agent.name, "version": runtime.root_agent.version}

    @app.post("/conversations")
    async def start_conversation(body: MessageRequest) -> StreamingResponse:
        """Start a conversation with its first message and stream that turn's events.

        The new conversation_id is in the X-Conversation-Id header and on every event.
        """
        conversation_id, events = await dispatcher.start(body.message_id, body.text)
        return sse_response(
            events,
            status_code=201,
            headers={"X-Conversation-Id": conversation_id, "Location": f"/conversations/{conversation_id}"},
        )

    @app.post("/conversations/{conversation_id}/messages")
    async def post_message(conversation_id: str, body: MessageRequest) -> StreamingResponse:
        """Send the next message of a conversation and stream the turn's events."""
        try:
            events = await dispatcher.send(conversation_id, body.message_id, body.text)
        except UnknownConversation:
            raise _not_found(conversation_id) from None
        except DuplicateMessage:
            raise HTTPException(
                status_code=409, detail=f"message_id '{body.message_id}' was already sent"
            ) from None
        return sse_response(events)

    @app.get("/conversations/{conversation_id}/events")
    async def replay_events(
        conversation_id: str, last_event_id: int = Header(default=0, alias="Last-Event-ID")
    ) -> StreamingResponse:
        """Replay buffered events after the client's last cursor, for a client that lost its stream."""
        try:
            missed = await dispatcher.replay(conversation_id, last_event_id)
        except UnknownConversation:
            raise _not_found(conversation_id) from None

        async def events() -> AsyncIterator[OutboundEvent]:
            for event in missed:
                yield event

        return sse_response(events())

    return app
