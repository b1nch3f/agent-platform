"""Model registry: which provider serves each model name (docs/design/05, "Model registry").

A YAML file for now:

    models:
      claude-haiku-5-5: { provider: anthropic }
      fast:             { provider: gemini, model: gemini-3.8-flash }   # an alias

An agent definition names a registered model. Only the gateway knows the provider behind it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from ap_gateway.model import ModelAdapter, UnknownModelError


def _anthropic() -> ModelAdapter:
    from ap_gateway.anthropic_adapter import AnthropicAdapter

    return AnthropicAdapter()


def _gemini() -> ModelAdapter:
    from ap_gateway.gemini_adapter import GeminiAdapter

    return GeminiAdapter()


# Provider SDKs are imported, and their clients created, only when a provider is first used.
PROVIDERS: dict[str, Callable[[], ModelAdapter]] = {"anthropic": _anthropic, "gemini": _gemini}


class RegisteredModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str | None = None  # the provider's model id; defaults to the registered name


class _RegistryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: dict[str, RegisteredModel]


class ModelRegistry:
    def __init__(
        self,
        models: dict[str, RegisteredModel],
        providers: dict[str, Callable[[], ModelAdapter]] | None = None,
    ):
        self._models = models
        self._providers = providers if providers is not None else PROVIDERS
        self._adapters: dict[str, ModelAdapter] = {}
        for name, entry in models.items():
            if entry.provider not in self._providers:
                raise ValueError(
                    f"Model '{name}': unknown provider '{entry.provider}'. Known: {', '.join(sorted(self._providers))}"
                )

    @classmethod
    def from_file(cls, path: str | Path) -> ModelRegistry:
        return cls(_RegistryFile.model_validate(yaml.safe_load(Path(path).read_text())).models)

    def resolve(self, name: str) -> tuple[ModelAdapter, str]:
        """The adapter that serves a registered model, and the provider's id for it."""
        entry = self._models.get(name)
        if entry is None:
            raise UnknownModelError(
                f"Model '{name}' is not registered. Registered: {', '.join(sorted(self._models)) or 'none'}"
            )
        if entry.provider not in self._adapters:
            self._adapters[entry.provider] = self._providers[entry.provider]()
        return self._adapters[entry.provider], entry.model or name
