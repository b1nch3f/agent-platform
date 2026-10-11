"""HTTP + SSE transport: the network layer over the Dispatcher (docs/design/03)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ap_contracts import CallIdentity, OutboundEvent
from ap_sdk import Runtime
from ap_transport.dispatcher import DEV_IDENTITY, Dispatcher, current_trace_id


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(min_length=1, description="Client-generated; a repeat is rejected with 409.")
    text: str = Field(min_length=1)


def to_sse(event: OutboundEvent) -> str:
    """One Server-Sent Event. The cursor is the SSE id, so a client resumes with Last-Event-ID."""
    return f"id: {event.cursor}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


def create_app(runtime: Runtime, identity: CallIdentity = DEV_IDENTITY) -> FastAPI:
    app = FastAPI(title="Agent Platform transport")
    dispatcher = Dispatcher(runtime, protocol="http_sse", identity=identity)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "agent": runtime.root_agent.name, "version": runtime.root_agent.version}

    @app.post("/conversations", status_code=201)
    async def create_conversation() -> dict:
        return {"conversation_id": dispatcher.new_conversation_id()}

    @app.post("/conversations/{conversation_id}/messages")
    async def post_message(conversation_id: str, body: MessageRequest) -> StreamingResponse:
        """Send one message and stream the turn's events back on the same connection."""
        if await dispatcher.is_duplicate(conversation_id, body.message_id):
            raise HTTPException(status_code=409, detail=f"message_id '{body.message_id}' was already sent")

        async def stream() -> AsyncIterator[str]:
            async for event in dispatcher.send(conversation_id, body.message_id, body.text):
                yield to_sse(event)

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        if trace_id := current_trace_id():
            headers["X-Trace-Id"] = trace_id
        return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)

    @app.get("/conversations/{conversation_id}/events")
    async def replay_events(
        conversation_id: str, last_event_id: int = Header(default=0, alias="Last-Event-ID")
    ) -> StreamingResponse:
        """Replay buffered events after the client's last cursor, for a client that lost its stream."""
        missed = dispatcher.replay(conversation_id, last_event_id)

        async def stream() -> AsyncIterator[str]:
            for event in missed:
                yield to_sse(event)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    return app
