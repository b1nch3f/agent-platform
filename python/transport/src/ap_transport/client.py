"""Client SDK: send a message and read the turn's events."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx

from ap_contracts import OutboundEvent


def parse_sse(lines: Iterator[str]) -> Iterator[OutboundEvent]:
    """Parse a Server-Sent Events stream. Each event's data is one OutboundEvent."""
    data: list[str] = []
    for line in lines:
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif line == "" and data:
            yield OutboundEvent.model_validate_json("\n".join(data))
            data = []


class Client:
    def __init__(self, base_url: str = "http://localhost:8000", *, http: httpx.Client | None = None):
        self._http = http or httpx.Client(base_url=base_url, timeout=httpx.Timeout(10.0, read=None))

    def create_conversation(self) -> str:
        response = self._http.post("/conversations")
        response.raise_for_status()
        return response.json()["conversation_id"]

    def send(
        self, conversation_id: str, text: str, *, message_id: str | None = None
    ) -> Iterator[OutboundEvent]:
        """Send one message; yields events until the turn ends. A message_id is generated if not given."""
        body = {"message_id": message_id or uuid.uuid4().hex, "text": text}
        with self._http.stream("POST", f"/conversations/{conversation_id}/messages", json=body) as response:
            if response.status_code != 200:
                response.read()
                response.raise_for_status()
            yield from parse_sse(response.iter_lines())
