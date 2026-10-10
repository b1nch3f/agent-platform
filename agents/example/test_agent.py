"""Tests for the example agent, using a scripted model: no API key, same result every run.

The script says what the model "decides"; the tests check that the agent's own code
(its tools, callbacks and definition) does the right thing in response.
"""

from pathlib import Path

import pytest

from ap_sdk import load_agent
from ap_sdk.testing import run_turn, scripted_runtime, text_of, tool_statuses

AGENT = Path(__file__).parent / "agent.yaml"


def weather_call(city: str) -> dict:
    return {"tool_call": {"name": "get_weather", "arguments": {"city": city}}}


@pytest.fixture
def agent():
    return load_agent(AGENT)


async def test_weather_question_calls_the_tool_and_answers(agent):
    runtime, model = scripted_runtime(agent, [[weather_call("Pune")], [{"text": "It is 29°C in Pune."}]])

    events = await run_turn(runtime, "What's the weather in Pune?")

    assert tool_statuses(events) == [("get_weather", "started"), ("get_weather", "completed")]
    assert text_of(events) == "It is 29°C in Pune."
    # What the tool returned to the model:
    result = model.requests[1].messages[-1].content[0]
    assert result.content == '{"city": "Pune", "temperature_c": 29, "conditions": "partly cloudy"}'


async def test_unknown_city_is_reported_to_the_model_as_a_tool_error(agent):
    runtime, model = scripted_runtime(
        agent, [[weather_call("Atlantis")], [{"text": "I have no data for Atlantis."}]]
    )

    events = await run_turn(runtime, "Weather in Atlantis?")

    assert tool_statuses(events)[-1] == ("get_weather", "failed")
    result = model.requests[1].messages[-1].content[0]
    assert result.is_error and "No weather data for 'Atlantis'" in result.content


async def test_model_is_given_the_instructions_and_the_tool(agent):
    runtime, model = scripted_runtime(agent, [[{"text": "Hello."}]])

    await run_turn(runtime, "Hi")

    request = model.requests[0]
    assert "never guess a temperature" in request.system
    assert [t.name for t in request.tools] == ["get_weather"]
    assert request.tools[0].input_schema["required"] == ["city"]


async def test_agent_callback_runs_before_each_invocation(agent, caplog):
    runtime, _ = scripted_runtime(agent, [[{"text": "Hello."}]])

    with caplog.at_level("INFO", logger="weather-assistant"):
        await run_turn(runtime, "Hi")

    assert [r.message for r in caplog.records if r.name == "weather-assistant"] == ["question received"]
