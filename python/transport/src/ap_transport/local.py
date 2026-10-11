"""In-process client: talk to an agent running in this process, with no network in between."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator

from ap_contracts import OutboundEvent
from ap_sdk import Runtime
from ap_transport.dispatcher import Dispatcher


class LocalClient:
    """Backed by the same Dispatcher the HTTP app uses, so identifiers, cursors and event
    order are identical whether an agent runs locally or is served."""

    def __init__(self, runtime: Runtime):
        self._dispatcher = Dispatcher(runtime, protocol="local")
        # The runtime is async; this client is not. It owns one event loop and drives it per event.
        self._loop = asyncio.new_event_loop()

    def start(self, text: str, *, message_id: str | None = None) -> tuple[str, Iterator[OutboundEvent]]:
        """Start a conversation with its first message. Returns the new conversation_id and the
        turn's events. A message_id is generated if not given."""
        conversation_id, events = self._loop.run_until_complete(
            self._dispatcher.start(message_id or uuid.uuid4().hex, text)
        )
        return conversation_id, self._drain(events)

    def send(
        self, conversation_id: str, text: str, *, message_id: str | None = None
    ) -> Iterator[OutboundEvent]:
        """Send the next message of a conversation; yields events as the turn produces them.

        Raises UnknownConversation or DuplicateMessage (from ap_transport.dispatcher) before running anything.
        """
        events = self._loop.run_until_complete(
            self._dispatcher.send(conversation_id, message_id or uuid.uuid4().hex, text)
        )
        return self._drain(events)

    def _drain(self, events: AsyncIterator[OutboundEvent]) -> Iterator[OutboundEvent]:
        # The whole turn runs in one task, feeding a queue this method drains. Driving the event
        # stream step by step from here would run each step in a new task, and tracing spans
        # must be opened and closed in the same one.
        queue: asyncio.Queue[OutboundEvent | None] = asyncio.Queue()

        async def run_turn() -> None:
            try:
                async for event in events:
                    queue.put_nowait(event)
            finally:
                queue.put_nowait(None)

        turn = self._loop.create_task(run_turn())
        try:
            while (event := self._loop.run_until_complete(queue.get())) is not None:
                yield event
        finally:
            if not turn.done():  # the caller stopped reading early
                turn.cancel()
            self._loop.run_until_complete(asyncio.gather(turn, return_exceptions=True))

    def close(self) -> None:
        self._loop.close()
