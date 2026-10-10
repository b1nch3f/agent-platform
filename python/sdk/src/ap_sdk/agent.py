"""An agent: its definition, its tools and its callbacks."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ap_contracts import AgentDefinition
from ap_sdk.callbacks import Callbacks
from ap_sdk.tools import Tool


@dataclass
class Agent:
    definition: AgentDefinition
    tools: list[Tool] = field(default_factory=list)
    callbacks: Callbacks = field(default_factory=Callbacks)

    def __post_init__(self) -> None:
        declared = {ref.name for ref in self.definition.tools}
        provided = {t.name for t in self.tools}
        if declared != provided:
            raise ValueError(
                f"Agent '{self.name}': definition declares tools {sorted(declared)}, got {sorted(provided)}"
            )

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def version(self) -> str:
        return self.definition.version


def _resolve(reference: str, base_dir: Path) -> Any:
    """Resolve 'module:attribute', where the module is a .py file next to the definition."""
    module_name, _, attribute = reference.partition(":")
    if not module_name or not attribute:
        raise ValueError(f"Expected 'module:attribute', got '{reference}'")
    path = base_dir / f"{module_name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"'{reference}': no file {path}")
    # Keyed by path, so two agents can each have their own tools.py.
    qualified = f"ap_agent_{abs(hash(str(path.resolve())))}_{module_name}"
    module = sys.modules.get(qualified)
    if module is None:
        spec = importlib.util.spec_from_file_location(qualified, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
    try:
        return getattr(module, attribute)
    except AttributeError:
        raise AttributeError(f"'{reference}': {path} has no attribute '{attribute}'") from None


def load_agent(path: str | Path) -> Agent:
    """Load an agent from a YAML definition. Tool and callback references resolve next to the file."""
    path = Path(path)
    definition = AgentDefinition.model_validate(yaml.safe_load(path.read_text()))

    tools = []
    for ref in definition.tools:
        resolved = _resolve(ref.function, path.parent)
        if not isinstance(resolved, Tool):
            raise TypeError(f"'{ref.function}' is not a tool; decorate the function with @tool")
        if resolved.name != ref.name:
            raise ValueError(
                f"Tool '{ref.name}' points at '{ref.function}', which is named '{resolved.name}'"
            )
        tools.append(resolved)

    callbacks = Callbacks()
    if definition.callbacks:
        callbacks = _resolve(definition.callbacks, path.parent)
        if not isinstance(callbacks, Callbacks):
            raise TypeError(f"'{definition.callbacks}' is not a Callbacks object")

    return Agent(definition=definition, tools=tools, callbacks=callbacks)
