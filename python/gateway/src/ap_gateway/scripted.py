"""A fake model that replays a script. Used by tests and conformance cases; needs no API key."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ap_contracts import ModelDone, ModelEvent, ModelRequest, TextDelta, ToolCall, Usage


class ScriptExhaustedError(RuntimeError):
    pass


class ScriptedModel:
    """Each model call replays the next turn of the script and records the request it received.

    A turn is a list of steps:
      {"text": "..."}                                         one text delta
      {"tool_call": {"name": ..., "arguments": {...}}}        one tool call; "id" is optional
      {"stop_reason": "max_tokens"}                           overrides the inferred stop reason
      {"error": "..."}                                        the provider call fails at this point
    The stop reason defaults to "tool_use" if the turn has a tool call, otherwise "end_turn".
    """

    provider = "scripted"

    def __init__(self, turns: list[list[dict[str, Any]]]):
        self._turns = list(turns)
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        call_index = len(self.requests)
        self.requests.append(request.model_copy(deep=True))
        if call_index >= len(self._turns):
            raise ScriptExhaustedError(
                f"Model called {call_index + 1} times; script has {len(self._turns)} turns"
            )

        stop_reason = None
        has_tool_call = False
        for step_index, step in enumerate(self._turns[call_index]):
            if "text" in step:
                yield TextDelta(text=step["text"])
            elif "tool_call" in step:
                call = step["tool_call"]
                has_tool_call = True
                yield ToolCall(
                    id=call.get("id", f"call_{call_index}_{step_index}"),
                    name=call["name"],
                    arguments=call.get("arguments", {}),
                )
            elif "stop_reason" in step:
                stop_reason = step["stop_reason"]
            elif "error" in step:
                raise RuntimeError(step["error"])
            else:
                raise ValueError(f"Unknown script step: {step}")
        yield ModelDone(
            stop_reason=stop_reason or ("tool_use" if has_tool_call else "end_turn"),
            usage=Usage(input_tokens=0, output_tokens=0),
        )
