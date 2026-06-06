"""Sandbox agent runner for prompt testing.

Replaces HTTP tool calls with closures over synthetic scenario data,
runs the full Claude tool-calling loop, and returns a RunResult.
"""
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is on sys.path when this module is imported directly.
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import jinja2
from anthropic import Anthropic

from app.ai_tools import tool_schemas
from app.config import AIConfig

_MPH_PER_MS = 2.23694


@dataclass
class RunResult:
    scenario_name: str
    prompt_variant: str
    action_taken: str      # "deploy" | "retract" | "none" | "error"
    report_text: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    tool_calls: List[str]  # ordered tool names called
    error: Optional[str] = None


def _settings_table(cfg: AIConfig) -> str:
    lines = ["| Setting | Value |", "|---------|-------|"]
    lines += [f"| {k} | {v} |" for k, v in cfg.model_dump().items()]
    return "\n".join(lines)


def _render_prompt(prompt_path: Path, cfg: AIConfig) -> str:
    env = jinja2.Environment(
        loader=jinja2.BaseLoader(),
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template_text = prompt_path.read_text()
    return env.from_string(template_text).render(
        settings_table=_settings_table(cfg),
        **cfg.model_dump(),
    )


def _make_mock_dispatch(
    scenario: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """Return (dispatch_dict, action_log) bound to the scenario's synthetic data."""
    obs = scenario["current_obs"]
    action_log: List[str] = []

    def get_weather() -> str:
        return json.dumps({
            "timestamp": scenario["current_time"],
            "air_temp_c": obs["air_temp_c"],
            "rain_prev_min_mm": obs["rain_prev_min_mm"],
            "lightning_avg_dist_km": 0,
            "wind_speed_mph": round(obs["wind_avg_m_s"] * _MPH_PER_MS, 2),
        })

    def get_wind() -> str:
        return json.dumps({
            "timestamp": scenario["current_time"],
            "wind_speed_mph": round(obs["wind_avg_m_s"] * _MPH_PER_MS, 2),
        })

    def get_weather_history(minutes: int = 60) -> str:
        history = scenario.get("history", [])
        # Convert wind to mph for each entry
        enriched = []
        for entry in history:
            e = dict(entry)
            if "wind_avg_m_s" in e:
                e["wind_speed_mph"] = round(e["wind_avg_m_s"] * _MPH_PER_MS, 2)
            enriched.append(e)
        return json.dumps(enriched)

    def get_forecast(period: str = "hourly") -> str:
        return json.dumps(scenario.get("forecast", []))

    def get_awning_status() -> str:
        return json.dumps({"state": scenario["awning_state"]})

    def deploy_awning(seconds: int = 3) -> str:
        action_log.append(f"deploy:{seconds}s")
        return f"Awning deployed for {seconds} seconds."

    def retract_awning(seconds: Optional[int] = None) -> str:
        label = f"retract:{seconds}s" if seconds is not None else "retract:full"
        action_log.append(label)
        if seconds is not None:
            return f"Awning retracted for {seconds} seconds."
        return "Awning fully retracted."

    def log_awning_action(action: str, reason: str) -> str:
        action_log.append(f"log:{action}")
        return f"Logged action '{action}': {reason}"

    dispatch = {
        "get_weather": get_weather,
        "get_wind": get_wind,
        "get_weather_history": get_weather_history,
        "get_forecast": get_forecast,
        "get_awning_status": get_awning_status,
        "deploy_awning": deploy_awning,
        "retract_awning": retract_awning,
        "log_awning_action": log_awning_action,
    }
    return dispatch, action_log


def _extract_action(action_log: List[str]) -> str:
    """Return the last physical action taken, or 'none'."""
    for entry in reversed(action_log):
        if entry.startswith("deploy:"):
            return "deploy"
        if entry.startswith("retract:"):
            return "retract"
    return "none"


def _call_tool(dispatch: Dict[str, Any], name: str, inputs: Dict[str, Any]) -> str:
    fn = dispatch.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        return fn(**inputs)
    except Exception as exc:
        return f"Tool error: {exc}"


def run_scenario(
    scenario: Dict[str, Any],
    prompt_path: Path,
    cfg: AIConfig,
    model: str,
) -> RunResult:
    """Run the full agent loop against synthetic scenario data and return a RunResult."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    client = Anthropic(api_key=api_key)

    try:
        system_text = _render_prompt(prompt_path, cfg)
    except Exception as exc:
        return RunResult(
            scenario_name=scenario["name"],
            prompt_variant=prompt_path.name,
            action_taken="error",
            report_text="",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            tool_calls=[],
            error=f"Prompt render error: {exc}",
        )

    system_blocks = [
        {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}
    ]

    dispatch, action_log = _make_mock_dispatch(scenario)
    messages = [
        {"role": "user", "content": "Determine if the patio awning should be extended or retracted."}
    ]

    total_input = 0
    total_output = 0
    tool_call_names: List[str] = []
    report_text = ""

    try:
        while True:
            response = client.messages.create(
                model=model,
                max_tokens=8000,
                messages=messages,
                system=system_blocks,
                tools=tool_schemas,
                temperature=0,
            )
            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens

            # Serialize content blocks for the message history
            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            messages.append({"role": "assistant", "content": assistant_content})

            if response.stop_reason == "end_turn":
                report_text = "\n".join(
                    b.text for b in response.content if b.type == "text"
                )
                break

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_call_names.append(block.name)
                    result = _call_tool(dispatch, block.name, dict(block.input))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            if not tool_results:
                # stop_reason was not end_turn but no tool calls either — safety break
                break

            messages.append({"role": "user", "content": tool_results})

    except Exception as exc:
        return RunResult(
            scenario_name=scenario["name"],
            prompt_variant=prompt_path.name,
            action_taken="error",
            report_text=report_text,
            input_tokens=total_input,
            output_tokens=total_output,
            total_tokens=total_input + total_output,
            tool_calls=tool_call_names,
            error=str(exc),
        )

    action_taken = _extract_action(action_log)
    return RunResult(
        scenario_name=scenario["name"],
        prompt_variant=prompt_path.name,
        action_taken=action_taken,
        report_text=report_text,
        input_tokens=total_input,
        output_tokens=total_output,
        total_tokens=total_input + total_output,
        tool_calls=tool_call_names,
    )
