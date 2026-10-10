"""Tools and callbacks for the example agent."""

import logging

from ap_sdk import Callbacks, tool

logger = logging.getLogger("weather-assistant")

# Canned data, so the example needs no network and gives the same answer every time.
_WEATHER = {
    "pune": {"temperature_c": 29, "conditions": "partly cloudy"},
    "mumbai": {"temperature_c": 32, "conditions": "humid"},
    "bengaluru": {"temperature_c": 24, "conditions": "light rain"},
    "delhi": {"temperature_c": 35, "conditions": "clear"},
}


@tool
def get_weather(city: str) -> dict:
    """Current weather for a city. Covers Pune, Mumbai, Bengaluru and Delhi."""
    report = _WEATHER.get(city.strip().lower())
    if report is None:
        raise LookupError(f"No weather data for '{city}'")
    return {"city": city, **report}


callbacks = Callbacks()


@callbacks.on("before_agent")
def log_question(ctx) -> None:
    """An agent-level callback: runs before each invocation of this agent."""
    logger.info("question received", extra={"conversation_id": ctx.conversation_id})
