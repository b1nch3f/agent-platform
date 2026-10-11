"""In-process client: talk to an agent running in this process, with no network in between."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

from ap_contracts import OutboundEvent
from ap_sdk import Runtime
from ap_transport.dispatcher import Dispatcher


class DuplicateMessage(Exception):
    """This message_id was already sent in the conversation."""


class LocalClient:
    """Backed by the same Dispatcher the HTTP app uses, so identifiers, cursors and event
    order are identical whether an agent runs locally or is served."""

    def __init__(self, runtime: Runtime):
        self._dispatcher = Dispatcher(runtime, protocol="local")
        # The runtime is async; this client is not. It owns one event loop and drives it per event.
        self._loop = asyncio.new_event_loop()

    def create_conversation(self) -> str:
        return self._dispatcher.new_conversation_id()

    def send(
        self, conversation_id: str, text: str, *, message_id: str | None = None
    ) -> Iterator[OutboundEvent]:
        """Send one message; yields events as the turn produces them. A message_id is generated if not given."""
        message_id = message_id or uuid.uuid4().hex
        if self._loop.run_until_complete(self._dispatcher.is_duplicate(conversation_id, message_id)):
            raise DuplicateMessage(message_id)
        events = self._dispatcher.send(conversation_id, message_id, text)
        try:
            while True:
                try:
                    yield self._loop.run_until_complete(anext(events))
                except StopAsyncIteration:
                    return
        finally:
            self._loop.run_until_complete(events.aclose())

    def close(self) -> None:
        self._loop.close()
