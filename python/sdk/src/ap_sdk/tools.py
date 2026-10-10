"""Tool SDK: turn a typed Python function into a tool the model can call."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model

from ap_contracts import ToolSpec


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    input_model: type[BaseModel]

    def spec(self) -> ToolSpec:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(name=self.name, description=self.description, input_schema=schema)

    async def handler(self, arguments: dict[str, Any]) -> str:
        """Validate the model's arguments against the signature, run the function, return text.

        Raises pydantic.ValidationError on bad arguments; the tool gateway turns that into an
        error result the model can read and correct.
        """
        validated = self.input_model.model_validate(arguments)
        kwargs = {name: getattr(validated, name) for name in self.input_model.model_fields}
        result = self.fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, str) else json.dumps(result, default=str)


def tool(fn: Callable[..., Any] | None = None, *, name: str | None = None, description: str | None = None):
    """Decorator. The input schema comes from the type hints, the description from the docstring.

    @tool
    def get_weather(city: str) -> str:
        \"\"\"Current weather for a city.\"\"\"
    """

    def wrap(f: Callable[..., Any]) -> Tool:
        fields: dict[str, Any] = {}
        for param in inspect.signature(f).parameters.values():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                raise TypeError(f"Tool '{f.__name__}' cannot take *args or **kwargs")
            if param.annotation is param.empty:
                raise TypeError(f"Tool '{f.__name__}': parameter '{param.name}' needs a type hint")
            fields[param.name] = (param.annotation, ... if param.default is param.empty else param.default)
        input_model = create_model(f"{f.__name__}_input", __config__=ConfigDict(extra="forbid"), **fields)
        return Tool(
            name=name or f.__name__,
            description=description or inspect.getdoc(f) or "",
            fn=f,
            input_model=input_model,
        )

    return wrap(fn) if fn is not None else wrap
