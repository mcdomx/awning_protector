import json
import os

import requests
from anthropic.types import ToolParam
from dotenv import load_dotenv

from .awning import awning_client
from .log_store import log_store

load_dotenv()

WEATHER_URL = os.getenv("WEATHER_URL", "http://localhost:8766").rstrip("/")
AWNING_URL = os.getenv("AWNING_URL", "http://localhost:8765").rstrip("/")


def _normalize_action(action: str) -> str:
    action_lower = action.lower()
    if action_lower.startswith("deployed"):
        return "deploy"
    if action_lower.startswith("retracted"):
        return "undeploy"
    return None


def deploy_awning(seconds: int = 3) -> str:
    resp = requests.get(
        f"{AWNING_URL}/awning/deploy/timed",
        params={"seconds": seconds},
        timeout=30,
    )
    if resp.ok:
        awning_client.current_state = "deployed"
    return f"Deployed awning by {seconds} seconds."


def retract_awning(seconds: int = None) -> str:
    if seconds:
        resp = requests.get(
            f"{AWNING_URL}/awning/undeploy/timed",
            params={"seconds": seconds},
            timeout=30,
        )
        if resp.ok:
            awning_client.current_state = "undeployed"
        return f"Retracted awning by {seconds} seconds."
    resp = requests.get(f"{AWNING_URL}/awning/undeploy", timeout=30)
    if resp.ok:
        awning_client.current_state = "undeployed"
    return "Retracted awning."


def get_weather() -> str:
    _fields = ("timestamp", "air_temp_c", "rain_prev_min_mm", "lightning_avg_dist_km")
    obs = requests.get(f"{WEATHER_URL}/weather/latest", timeout=10).json()
    return json.dumps({k: v for k, v in obs.items() if k in _fields})


def get_wind() -> str:
    _fields = ("timestamp", "wind_speed_mph")
    obs = requests.get(f"{WEATHER_URL}/weather/wind", timeout=10).json()
    return json.dumps({k: v for k, v in obs.items() if k in _fields})


def get_weather_history(minutes: int = 60) -> str:
    obs = requests.get(
        f"{WEATHER_URL}/weather/history",
        params={"minutes": minutes},
        timeout=10,
    ).json()
    return json.dumps(obs["observations"])


def get_forecast(period: str = "hourly") -> str:
    if period not in ("hourly", "daily"):
        return "The 'period' attribute value can only be 'hourly' or 'daily'"
    obs = requests.get(f"{WEATHER_URL}/weather/forecast/{period}", timeout=10).json()
    return json.dumps(obs["forecast"])


def log_awning_action(action: str, reason: str) -> str:
    normalized = _normalize_action(action)
    log_store.add_automation(
        "ai_agent",
        reason,
        triggered=True,
        action_taken=normalized,
    )
    return f"Logged: {action} ({reason})"


def get_awning_status() -> str:
    return awning_client.current_state or "retracted"


def execute_tool(name: str, tool_input: dict) -> str:
    dispatch = {
        "get_weather": get_weather,
        "get_wind": get_wind,
        "get_weather_history": get_weather_history,
        "get_forecast": get_forecast,
        "deploy_awning": deploy_awning,
        "retract_awning": retract_awning,
        "log_awning_action": log_awning_action,
        "get_awning_status": get_awning_status,
    }
    if name not in dispatch:
        raise ValueError(f"Unknown tool: {name}")
    tool_input = {k: v for k, v in tool_input.items() if k != "file_path"}
    return dispatch[name](**tool_input)


tool_schemas = [
    ToolParam({
        "name": "get_weather",
        "description": "Get key weather elements from the weather station on the patio at my home. Returns current temperature, rainfall, and lightning distance. Reports update every minute.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }),
    ToolParam({
        "name": "get_wind",
        "description": "Get wind speed from the weather station on the patio at my home. Reports update every 15 seconds.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }),
    ToolParam({
        "name": "deploy_awning",
        "description": "Deploys the awning by a specified number of seconds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "The number of seconds to deploy the awning. Defaults to 3.",
                    "default": 3,
                }
            },
            "required": [],
        },
    }),
    ToolParam({
        "name": "retract_awning",
        "description": "Retracts the awning. Pass a number of seconds to partially retract, or omit the parameter to fully retract the awning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "The number of seconds to retract the awning. Omit to fully retract.",
                }
            },
            "required": [],
        },
    }),
    ToolParam({
        "name": "log_awning_action",
        "description": "Log a timestamped entry for awning actions. Each log entry records the action taken and the reason for the action.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "The awning action to log. Valid formats: 'deployed {seconds}' "
                        "(seconds is an integer), 'retracted' (fully retracted), "
                        "or 'retracted {seconds}' (seconds is an integer)."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "A brief explanation for why the action was taken.",
                },
            },
            "required": ["action", "reason"],
        },
    }),
    ToolParam({
        "name": "get_awning_status",
        "description": (
            "Determine the current status of the awning. "
            "Returns 'deployed' or 'retracted'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }),
    ToolParam({
        "name": "get_weather_history",
        "description": "Retrieve weather observations from the local weather station for a specified time window. Defaults to the last 60 minutes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes": {
                    "type": "integer",
                    "description": "Number of minutes of historical observations to retrieve. Defaults to 60.",
                    "default": 60,
                }
            },
            "required": [],
        },
    }),
    ToolParam({
        "name": "get_forecast",
        "description": "Get a weather forecast based on the tempest location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["hourly", "daily"],
                    "description": "The forecast period type. Defaults to 'hourly'.",
                    "default": "hourly",
                }
            },
            "required": [],
        },
    }),
]
