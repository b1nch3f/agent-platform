"""Callbacks: hooks at four levels of a turn (docs/design/04, "Callbacks").

| Level   | Fires                        | Before                         | After                               |
| ------- | ---------------------------- | ------------------------------ | ----------------------------------- |
| runtime | once per message             | before_runtime(ctx)            | after_runtime(ctx)                  |
| agent   | each agent invocation        | before_agent(ctx)              | after_agent(ctx)                    |
| model   | each model call              | before_model(ctx, request)     | after_model(ctx, message, done)     |
| tool    | each tool call               | before_tool(ctx, call)         | after_tool(ctx, call, result)       |

Each level also has an on_<level>_error(ctx, exc) hook, called when that level fails.

Callbacks may be sync or async. Some may return a value to change what happens next:
- before_model may return a ModelRequest to send instead.
- before_tool may return a ToolCallResult; the tool is then not called.
- after_tool may return a ToolCallResult to use instead.

Callbacks give early, in-process checks. The gateway remains the authoritative enforcement point.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

HOOKS = (
    "before_runtime",
    "after_runtime",
    "on_runtime_error",
    "before_agent",
    "after_agent",
    "on_agent_error",
    "before_model",
    "after_model",
    "on_model_error",
    "before_tool",
    "after_tool",
    "on_tool_error",
)

Callback = Callable[..., Any]


@dataclass
class Callbacks:
    before_runtime: list[Callback] = field(default_factory=list)
    after_runtime: list[Callback] = field(default_factory=list)
    on_runtime_error: list[Callback] = field(default_factory=list)
    before_agent: list[Callback] = field(default_factory=list)
    after_agent: list[Callback] = field(default_factory=list)
    on_agent_error: list[Callback] = field(default_factory=list)
    before_model: list[Callback] = field(default_factory=list)
    after_model: list[Callback] = field(default_factory=list)
    on_model_error: list[Callback] = field(default_factory=list)
    before_tool: list[Callback] = field(default_factory=list)
    after_tool: list[Callback] = field(default_factory=list)
    on_tool_error: list[Callback] = field(default_factory=list)

    def on(self, hook: str) -> Callable[[Callback], Callback]:
        """Decorator: `@callbacks.on("before_agent")`."""
        if hook not in HOOKS:
            raise ValueError(f"Unknown hook '{hook}'. Hooks: {', '.join(HOOKS)}")

        def register(fn: Callback) -> Callback:
            getattr(self, hook).append(fn)
            return fn

        return register

    def then(self, other: Callbacks) -> Callbacks:
        """A new set that runs this set's callbacks first, then `other`'s."""
        return Callbacks(**{hook: [*getattr(self, hook), *getattr(other, hook)] for hook in HOOKS})

    async def run(self, hook: str, *args: Any) -> list[Any]:
        """Call every callback registered on `hook`, in order, and return what they returned."""
        return [await invoke(fn, *args) for fn in getattr(self, hook)]


async def invoke(fn: Callback, *args: Any) -> Any:
    """Call one callback, awaiting it if it is async."""
    result = fn(*args)
    if inspect.isawaitable(result):
        result = await result
    return result
