import pytest
from pydantic import ValidationError

from ap_contracts import CallContext, ModelRequest
from ap_gateway import ModelGateway, ModelRegistry, RegisteredModel, ScriptedModel, UnknownModelError

CONTEXT = CallContext(
    tenant="t",
    user_id="u",
    conversation_id="c1",
    message_id="m1",
    invocation_id="i1",
    invocation_type="agent",
    agent="a",
    agent_version="1",
)


def request(model: str) -> ModelRequest:
    return ModelRequest(context=CONTEXT, model=model, system="s", messages=[], max_tokens=100)


def test_registry_resolves_a_model_to_its_provider_and_provider_model():
    created = []

    def factory(name):
        def create():
            created.append(name)
            return ScriptedModel([])

        return create

    registry = ModelRegistry(
        {
            "model-a": RegisteredModel(provider="one"),
            "fast": RegisteredModel(provider="one", model="model-a"),
            "model-b": RegisteredModel(provider="two"),
        },
        providers={"one": factory("one"), "two": factory("two")},
    )

    assert created == []  # nothing is created until a provider is used
    adapter_a, model_a = registry.resolve("model-a")
    adapter_fast, model_fast = registry.resolve("fast")

    assert (model_a, model_fast) == ("model-a", "model-a")
    assert adapter_a is adapter_fast  # one adapter per provider
    assert created == ["one"]


def test_unregistered_model_is_an_error_that_lists_what_is_registered():
    registry = ModelRegistry({"model-a": RegisteredModel(provider="one")}, providers={"one": lambda: None})

    with pytest.raises(UnknownModelError, match="'nope' is not registered. Registered: model-a"):
        registry.resolve("nope")


def test_unknown_provider_is_rejected_when_the_registry_is_built():
    with pytest.raises(ValueError, match="unknown provider 'three'"):
        ModelRegistry({"m": RegisteredModel(provider="three")}, providers={"one": lambda: None})


def test_registry_file_is_loaded_and_validated(tmp_path):
    good = tmp_path / "models.yaml"
    good.write_text(
        "models:\n  claude-haiku-5-5: {provider: anthropic}\n  fast: {provider: gemini, model: g}\n"
    )
    bad = tmp_path / "bad.yaml"
    bad.write_text("models:\n  m: {provider: anthropic, api_key: oops}\n")

    registry = ModelRegistry.from_file(good)

    assert sorted(registry._models) == ["claude-haiku-5-5", "fast"]
    with pytest.raises(ValidationError):
        ModelRegistry.from_file(bad)


async def test_gateway_sends_the_provider_model_id_to_the_adapter():
    model = ScriptedModel([[{"text": "hi"}]])
    registry = ModelRegistry(
        {"fast": RegisteredModel(provider="fake", model="provider-model-7")},
        providers={"fake": lambda: model},
    )

    events = [e async for e in ModelGateway(registry).stream(request("fast"))]

    assert events[0].text == "hi"
    assert model.requests[0].model == "provider-model-7"


def test_the_repo_registry_file_is_valid():
    from pathlib import Path

    registry = ModelRegistry.from_file(Path(__file__).parents[2] / "models.yaml")

    assert "gemini-3.8-flash" in registry._models
