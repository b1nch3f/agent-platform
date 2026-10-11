"""Conversation store: conversations and their append-only message history (docs/design/06)."""

from __future__ import annotations

import asyncio
from typing import Protocol

from ap_contracts import Message


class DuplicateMessageError(Exception):
    """A message with this message_id is already in the conversation."""


class UnknownConversationError(Exception):
    """No conversation with this ID has been created."""


class ConversationStore(Protocol):
    async def create(self, conversation_id: str, *, tenant: str, user_id: str) -> None:
        """Record a new conversation and who started it."""
        ...

    async def exists(self, conversation_id: str, *, tenant: str, user_id: str) -> bool:
        """True if the conversation was created and belongs to this caller."""
        ...

    async def load(self, conversation_id: str) -> list[Message]:
        """All messages of the conversation, oldest first."""
        ...

    async def append(self, conversation_id: str, message: Message, *, message_id: str | None = None) -> int:
        """Append one message and return its sequence number, starting at 1.

        `message_id` is set on user messages; a repeat raises DuplicateMessageError.
        A conversation that was never created raises UnknownConversationError.
        """
        ...

    async def has_message(self, conversation_id: str, message_id: str) -> bool: ...


class InMemoryConversationStore:
    """Keeps conversations in process memory. They are lost on restart.

    Temporary: the SQL-backed store replaces it, and until then the "SQL first" rule is not met.
    """

    def __init__(self) -> None:
        self._owners: dict[str, tuple[str, str]] = {}
        self._messages: dict[str, list[Message]] = {}
        self._message_ids: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    async def create(self, conversation_id: str, *, tenant: str, user_id: str) -> None:
        async with self._lock:
            if conversation_id in self._owners:
                raise ValueError(f"Conversation '{conversation_id}' already exists")
            self._owners[conversation_id] = (tenant, user_id)
            self._messages[conversation_id] = []
            self._message_ids[conversation_id] = set()

    async def exists(self, conversation_id: str, *, tenant: str, user_id: str) -> bool:
        return self._owners.get(conversation_id) == (tenant, user_id)

    async def load(self, conversation_id: str) -> list[Message]:
        return [m.model_copy(deep=True) for m in self._messages.get(conversation_id, [])]

    async def append(self, conversation_id: str, message: Message, *, message_id: str | None = None) -> int:
        async with self._lock:
            if conversation_id not in self._owners:
                raise UnknownConversationError(conversation_id)
            seen = self._message_ids[conversation_id]
            if message_id is not None:
                if message_id in seen:
                    raise DuplicateMessageError(message_id)
                seen.add(message_id)
            messages = self._messages[conversation_id]
            messages.append(message.model_copy(deep=True))
            return len(messages)

    async def has_message(self, conversation_id: str, message_id: str) -> bool:
        return message_id in self._message_ids.get(conversation_id, set())
