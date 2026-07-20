import json
import os

import requests
from anthropic.types import ToolParam
from dotenv import load_dotenv

from .awning import awning_client
from .config import get_config
from .log_store import log_store

load_dotenv()

WEATHER_URL = os.getenv("WEATHER_URL", "http://localhost:8766").rstrip("/")
AWNING_URL = os.getenv("AWNING_URL", "http://localhost:8765").rstrip("/")
UV_SENSOR_URL = os.getenv("UV_SENSOR_URL", "http://uvsensor.local:8768").rstrip("/")

# Seconds of additional motor travel per increment while extending for glare — small enough
# to check the sensor frequently, matching the "stop as soon as the reading drops" requirement.
GLARE_DEPLOY_STEP_SECONDS = 2


def _normalize_action(action: str) -> str:
    action_lower = action.lower()
    if action_lower.startswith("deployed"):
        return "deploy"
    if action_lower.startswith("retracted"):
        return "undeploy"
    return None


def deploy_awning(seconds: int = 3) -> str:
    delta = awning_client.deploy_delta_seconds(seconds)
    if delta is None:
        extension = awning_client.deployed_seconds()
        current = "fully extended" if extension == float("inf") else f"{extension}s extension"
        return f"Awning already deployed ({current}); no further extension needed."

    resp = requests.get(
        f"{AWNING_URL}/awning/deploy/timed",
        params={"seconds": delta},
        timeout=30,
    )
    resp.raise_for_status()
    awning_client.record_deploy_extension(seconds)
    return f"Extended awning by {delta}s (now at {seconds}s total extension)."


def deploy_for_glare(max_seconds: int = None) -> str:
    ai_cfg = get_config().ai
    cap = ai_cfg.max_extended_deployment_seconds
    target_cap = min(max_seconds, cap) if max_seconds else cap

    current_target = awning_client.deployed_seconds()
    if current_target == float("inf"):
        return "Awning already fully extended; no further extension possible."

    while current_target < target_cap:
        current_target = min(current_target + GLARE_DEPLOY_STEP_SECONDS, target_cap)
        delta = awning_client.deploy_delta_seconds(current_target)
        if delta is None:
            break
        resp = requests.get(
            f"{AWNING_URL}/awning/deploy/timed",
            params={"seconds": delta},
            timeout=30,
        )
        resp.raise_for_status()
        awning_client.record_deploy_extension(current_target)

        try:
            reading = requests.get(f"{UV_SENSOR_URL}/uv/latest", timeout=5).json()
        except Exception:
            return (
                f"Extended awning to {current_target}s for glare; "
                "uv sensor became unreachable mid-adjustment, stopped extending."
            )

        lux = reading.get("illuminance_lux")
        if lux is not None and lux < ai_cfg.glare_lux_threshold:
            return f"Extended awning to {current_target}s; glare cleared (illuminance {lux} lux)."

    return f"Extended awning to {current_target}s (extended-deployment cap reached); glare may persist."


def retract_awning(seconds: int = None) -> str:
    if seconds:
        resp = requests.get(
            f"{AWNING_URL}/awning/undeploy/timed",
            params={"seconds": seconds},
            timeout=30,
        )
        resp.raise_for_status()
        awning_client.record_partial_retract(seconds)
        return f"Retracted awning by {seconds} seconds."
    resp = requests.get(f"{AWNING_URL}/awning/undeploy", timeout=30)
    resp.raise_for_status()
    awning_client.record_full_retract()
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
    if awning_client.current_state == "deployed":
        extension = awning_client.deployed_seconds()
        if extension == float("inf"):
            return "deployed (fully extended)"
        return f"deployed ({extension}s extension)"
    return awning_client.current_state or "retracted"


def execute_tool(name: str, tool_input: dict) -> str:
    dispatch = {
        "get_weather": get_weather,
        "get_wind": get_wind,
        "get_weather_history": get_weather_history,
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
]

_ACTION_TOOL_NAMES = {"deploy_awning", "retract_awning", "log_awning_action", "get_awning_status"}
action_tool_schemas = [s for s in tool_schemas if s["name"] in _ACTION_TOOL_NAMES]

# Not part of action_tool_schemas — only offered to the orchestrator for runs where
# app/ai_pipeline.py has already verified wind/rain risk is none and the presence gate passes.
glare_deploy_tool_schema = ToolParam({
    "name": "deploy_for_glare",
    "description": (
        "Incrementally extends the awning further than a normal deployment to block direct "
        "sunlight detected by the living-room glare sensor, checking the sensor after each "
        "increment and stopping as soon as the reading drops below the glare threshold or the "
        "extended-deployment cap is reached. Only ever offered when conditions are already "
        "verified to be exceptionally safe — do not attempt to replicate this with repeated "
        "deploy_awning calls."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "max_seconds": {
                "type": "integer",
                "description": (
                    "Upper bound on total extension seconds; clamped server-side to the "
                    "configured extended-deployment cap regardless of the value passed."
                ),
            }
        },
        "required": [],
    },
})

_ACTION_DISPATCH = {
    "deploy_awning": deploy_awning,
    "retract_awning": retract_awning,
    "log_awning_action": log_awning_action,
    "get_awning_status": get_awning_status,
    "deploy_for_glare": deploy_for_glare,
}


def execute_action_tool(name: str, tool_input: dict) -> str:
    if name not in _ACTION_DISPATCH:
        raise ValueError(f"Unknown action tool: {name}")
    tool_input = {k: v for k, v in tool_input.items() if k != "file_path"}
    return _ACTION_DISPATCH[name](**tool_input)
