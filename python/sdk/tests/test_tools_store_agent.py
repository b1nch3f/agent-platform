from pathlib import Path

import pytest
from pydantic import ValidationError

from ap_contracts import Message, TextBlock
from ap_sdk import (
    Callbacks,
    DuplicateMessageError,
    InMemoryConversationStore,
    UnknownConversationError,
    load_agent,
    tool,
)


def test_tool_schema_comes_from_the_signature_and_docstring():
    @tool
    def search(query: str, limit: int = 5) -> list:
        """Search the catalogue."""
        return []

    spec = search.spec()

    assert (spec.name, spec.description) == ("search", "Search the catalogue.")
    assert spec.input_schema["required"] == ["query"]
    assert spec.input_schema["properties"]["limit"]["default"] == 5
    assert spec.input_schema["additionalProperties"] is False


def test_tool_name_and_description_can_be_overridden():
    @tool(name="lookup", description="Look something up.")
    def f(x: int) -> int:
        return x

    assert (f.name, f.description) == ("lookup", "Look something up.")


def test_tool_parameters_need_type_hints():
    with pytest.raises(TypeError, match="needs a type hint"):

        @tool
        def bad(x):
            return x


async def test_tool_handler_validates_runs_and_serializes():
    @tool
    def double(n: int) -> dict:
        """Double."""
        return {"result": n * 2}

    assert await double.handler({"n": 4}) == '{"result": 8}'
    with pytest.raises(ValidationError):
        await double.handler({"n": 4, "extra": 1})


def message(text: str) -> Message:
    return Message(role="user", content=[TextBlock(text=text)])


OWNER = {"tenant": "t1", "user_id": "u1"}


async def new_store(*conversation_ids: str) -> InMemoryConversationStore:
    store = InMemoryConversationStore()
    for conversation_id in conversation_ids:
        await store.create(conversation_id, **OWNER)
    return store


async def test_store_appends_in_order_and_returns_sequence_numbers():
    store = await new_store("c1")

    assert await store.append("c1", message("a"), message_id="m1") == 1
    assert await store.append("c1", message("b")) == 2
    assert [m.content[0].text for m in await store.load("c1")] == ["a", "b"]
    assert await store.load("other") == []


async def test_store_rejects_a_repeated_message_id_per_conversation():
    store = await new_store("c1", "c2")
    await store.append("c1", message("a"), message_id="m1")

    with pytest.raises(DuplicateMessageError):
        await store.append("c1", message("again"), message_id="m1")
    assert await store.has_message("c1", "m1")
    assert not await store.has_message("c2", "m1")
    assert await store.append("c2", message("a"), message_id="m1") == 1


async def test_store_returns_copies():
    store = await new_store("c1")
    await store.append("c1", message("a"))

    (await store.load("c1"))[0].content[0].text = "changed"

    assert (await store.load("c1"))[0].content[0].text == "a"


async def test_conversation_must_be_created_before_messages_are_appended():
    store = InMemoryConversationStore()

    with pytest.raises(UnknownConversationError):
        await store.append("never-created", message("a"))


async def test_conversation_exists_only_for_the_caller_who_started_it():
    store = await new_store("c1")

    assert await store.exists("c1", **OWNER)
    assert not await store.exists("c1", tenant="t1", user_id="someone-else")
    assert not await store.exists("c1", tenant="other-tenant", user_id="u1")
    assert not await store.exists("c2", **OWNER)


async def test_conversation_cannot_be_created_twice():
    store = await new_store("c1")

    with pytest.raises(ValueError, match="already exists"):
        await store.create("c1", **OWNER)


def write_agent(tmp_path: Path, yaml_text: str, tools_py: str) -> Path:
    (tmp_path / "tools.py").write_text(tools_py)
    path = tmp_path / "agent.yaml"
    path.write_text(yaml_text)
    return path


AGENT_YAML = """
name: demo
version: "1"
model: fake
instructions: Be brief.
tools:
  - name: ping
    function: tools:ping
callbacks: tools:callbacks
"""

TOOLS_PY = """
from ap_sdk import Callbacks, tool

@tool
def ping() -> str:
    "Ping."
    return "pong"

callbacks = Callbacks()
not_a_tool = 1
"""


def test_load_agent_resolves_tools_and_callbacks_next_to_the_definition(tmp_path):
    agent = load_agent(write_agent(tmp_path, AGENT_YAML, TOOLS_PY))

    assert (agent.name, agent.version) == ("demo", "1")
    assert [t.name for t in agent.tools] == ["ping"]
    assert isinstance(agent.callbacks, Callbacks)
    assert agent.definition.max_steps == 10


def test_load_agent_rejects_a_reference_that_is_not_a_tool(tmp_path):
    path = write_agent(tmp_path, AGENT_YAML.replace("tools:ping", "tools:not_a_tool"), TOOLS_PY)
    with pytest.raises(TypeError, match="is not a tool"):
        load_agent(path)


def test_load_agent_rejects_unknown_fields(tmp_path):
    path = write_agent(tmp_path, AGENT_YAML + "temperature: 1\n", TOOLS_PY)
    with pytest.raises(ValidationError):
        load_agent(path)
