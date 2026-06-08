import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from .ai_agent import _Claude, _build_system_blocks
from .ai_tools import action_tool_schemas, execute_action_tool, get_weather_history
from .automation import MPH_PER_MS
from .awning import awning_client
from .weather import weather_client

_C_TO_F = lambda c: round(c * 9 / 5 + 32, 1)

# The awning's physical location (and the earliest/latest_auto_deployment clock
# strings such as "8AM"/"6PM" in AIConfig) are local US/Eastern time, while the
# weather service reports in UTC. All times shown to the LLM are normalized to
# this zone and labeled so the model can compare them directly.
_LOCAL_TZ = ZoneInfo("America/New_York")

_RISKY = {"moderate", "high"}


@dataclass
class WorkerResult:
    name: str
    assessment: Dict[str, Any]
    raw_response: str
    error: Optional[str] = None


def _parse_json(raw: str, worker_name: str) -> Tuple[Dict, Optional[str]]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"```[a-z]*\n?", "", text).strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end]), None
        except json.JSONDecodeError as exc:
            return {}, f"JSON parse error in {worker_name}: {exc}"
    return {}, f"No JSON found in {worker_name} response"


def _parse_next_eval(report: str, default: int) -> int:
    m = re.search(r"Next Eval In:\s*(\d+)", report, re.IGNORECASE)
    return int(m.group(1)) if m else default


def _skipped_worker(name: str, reason: str) -> WorkerResult:
    return WorkerResult(name, {"skipped": True, "reason": reason}, "")


def _gather_pipeline_context() -> Dict[str, Any]:
    history = json.loads(get_weather_history(minutes=60))
    return {
        "current_obs": weather_client.latest_obs,
        "history": history,
        "forecast": weather_client.forecast,
        "awning_state": awning_client.current_state,
        "current_time": datetime.now(_LOCAL_TZ).strftime("%-I:%M %p %Z, %a %b %d"),
    }


# ── Worker functions ──────────────────────────────────────────────────────────

def run_wind_worker(ctx: Dict, claude: _Claude, system_blocks: list) -> WorkerResult:
    obs = ctx["current_obs"]
    wind_mph = round(obs.get("wind_avg_m_s", 0.0) * MPH_PER_MS, 2)
    history = ctx.get("history", [])

    lines = [f"Current wind: {wind_mph} mph", "", "Wind history (oldest first):"]
    for e in history:
        ts = e.get("timestamp", "?")
        w_m_s = e.get("wind_avg_m_s", e.get("wind_avg", 0))
        w = round(w_m_s * MPH_PER_MS, 2)
        lines.append(f"  {ts}  {w} mph")
    user_msg = "\n".join(lines)

    msgs = []
    claude.add_user_message(msgs, user_msg)
    resp = claude.chat(msgs, system_blocks=system_blocks, temperature=0)
    raw = claude.text_from_message(resp)
    assessment, err = _parse_json(raw, "wind")
    return WorkerResult("wind", assessment, raw, err)


def run_rain_worker(ctx: Dict, claude: _Claude, system_blocks: list) -> WorkerResult:
    obs = ctx["current_obs"]
    history = ctx.get("history", [])

    lines = [
        f"Current precip_type   : {obs.get('precip_type', 0)} (0=none, 1=rain, 2=hail)",
        f"Current rain/prev min : {obs.get('rain_prev_min_mm', 0.0)} mm",
        "",
        "Precipitation history (oldest first):",
    ]
    for e in history:
        ts = e.get("timestamp", "?")
        pt = e.get("precip_type", 0)
        rm = e.get("rain_prev_min_mm", 0.0)
        lines.append(f"  {ts}  precip_type={pt}  rain={rm} mm")
    user_msg = "\n".join(lines)

    msgs = []
    claude.add_user_message(msgs, user_msg)
    resp = claude.chat(msgs, system_blocks=system_blocks, temperature=0)
    raw = claude.text_from_message(resp)
    assessment, err = _parse_json(raw, "rain")
    return WorkerResult("rain", assessment, raw, err)


def run_forecast_worker(ctx: Dict, claude: _Claude, system_blocks: list) -> WorkerResult:
    forecast = ctx.get("forecast", [])

    lines = [f"Current time: {ctx['current_time']}", "", "Forecast (hourly, starting ~1h from now):"]
    for e in forecast:
        try:
            ts = datetime.fromtimestamp(e.get("dt", 0), tz=timezone.utc).astimezone(_LOCAL_TZ).strftime("%-I:%M %p %Z")
        except Exception:
            ts = "?"
        desc = e.get("description", "?")
        pop = e.get("pop", 0)
        w = e.get("wind_mph", 0)
        tc = e.get("temp_c", "?")
        lines.append(f"  {ts}  {desc}  rain_prob={pop:.0%}  wind={w}mph  temp={tc}C")
    user_msg = "\n".join(lines)

    msgs = []
    claude.add_user_message(msgs, user_msg)
    resp = claude.chat(msgs, system_blocks=system_blocks, temperature=0)
    raw = claude.text_from_message(resp)
    assessment, err = _parse_json(raw, "forecast")
    return WorkerResult("forecast", assessment, raw, err)


def run_solar_worker(ctx: Dict, claude: _Claude, system_blocks: list) -> WorkerResult:
    obs = ctx["current_obs"]
    temp_f = _C_TO_F(obs.get("air_temp_c", 0.0))
    user_msg = "\n".join([
        f"Current time  : {ctx['current_time']}",
        f"Temperature   : {obs.get('air_temp_c')}°C  ({temp_f}°F)",
        f"Illuminance   : {obs.get('illuminance_lux')} lux",
        f"UV index      : {obs.get('uv_index')}",
    ])

    msgs = []
    claude.add_user_message(msgs, user_msg)
    resp = claude.chat(msgs, system_blocks=system_blocks, temperature=0)
    raw = claude.text_from_message(resp)
    assessment, err = _parse_json(raw, "solar")
    return WorkerResult("solar", assessment, raw, err)


# ── Coordinator ───────────────────────────────────────────────────────────────

def run_coordinator(
    wind: WorkerResult, rain: WorkerResult,
    forecast: WorkerResult, solar: WorkerResult,
    ctx: Dict, claude: _Claude, system_blocks: list,
) -> str:
    user_msg = "\n".join([
        f"AWNING STATE : {ctx['awning_state']}",
        f"CURRENT TIME : {ctx['current_time']}",
        "",
        "WIND WORKER:",
        json.dumps(wind.assessment, indent=2),
        "",
        "RAIN WORKER:",
        json.dumps(rain.assessment, indent=2),
        "",
        "FORECAST WORKER:",
        json.dumps(forecast.assessment, indent=2),
        "",
        "SOLAR WORKER:",
        json.dumps(solar.assessment, indent=2),
    ])
    msgs = []
    claude.add_user_message(msgs, user_msg)
    resp = claude.chat(msgs, system_blocks=system_blocks, temperature=0)
    return claude.text_from_message(resp)


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_orchestrator(brief: str, claude: _Claude, system_blocks: list) -> str:
    msgs = []
    claude.add_user_message(msgs, brief)

    while True:
        resp = claude.chat(
            msgs,
            system_blocks=system_blocks,
            temperature=0,
            tools=action_tool_schemas,
            streaming=True,
        )
        claude.add_assistant_message(msgs, resp)

        if resp.stop_reason == "end_turn":
            return claude.text_from_message(resp)

        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                result = execute_action_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        if not tool_results:
            return claude.text_from_message(resp)
        msgs.append({"role": "user", "content": tool_results})


# ── Pipeline entry point ──────────────────────────────────────────────────────

def run_ai_pipeline(cfg) -> dict:
    model = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
    claude = _Claude(model=model)
    ctx = _gather_pipeline_context()

    wind_blocks = _build_system_blocks("wind", cfg)
    rain_blocks = _build_system_blocks("rain", cfg)
    forecast_blocks = _build_system_blocks("forecast", cfg)
    solar_blocks = _build_system_blocks("solar", cfg)
    coordinator_blocks = _build_system_blocks("coordinator", cfg)
    orchestrator_blocks = _build_system_blocks("orchestrator", cfg)

    wind_r = run_wind_worker(ctx, claude, wind_blocks)
    rain_r = run_rain_worker(ctx, claude, rain_blocks)

    fast_path = (
        wind_r.assessment.get("risk") in _RISKY
        or rain_r.assessment.get("risk") in _RISKY
    )

    if fast_path:
        reason = "fast-path: wind or rain worker reported moderate/high risk"
        forecast_r = _skipped_worker("forecast", reason)
        solar_r = _skipped_worker("solar", reason)
        brief = "\n".join([
            f"AWNING STATE : {ctx['awning_state']}",
            f"CURRENT TIME : {ctx['current_time']}",
            "",
            "FAST-PATH ALERT — wind or rain risk is moderate/high; forecast and solar",
            "analysis were skipped to save tokens.",
            f"Wind risk : {wind_r.assessment.get('risk')} — {wind_r.assessment.get('reasoning', '')}",
            f"Rain risk : {rain_r.assessment.get('risk')} — {rain_r.assessment.get('reasoning', '')}",
            "",
            "Overall situation: MUST_RETRACT",
            "Primary driver: immediate wind/rain hazard",
            "Recommendation: retract immediately regardless of solar benefit.",
        ])
    else:
        forecast_r = run_forecast_worker(ctx, claude, forecast_blocks)
        solar_r = run_solar_worker(ctx, claude, solar_blocks)
        brief = run_coordinator(
            wind_r, rain_r, forecast_r, solar_r,
            ctx, claude, coordinator_blocks,
        )

    report = run_orchestrator(brief, claude, orchestrator_blocks)
    next_eval_seconds = _parse_next_eval(report, cfg.min_eval_interval_seconds)

    return {
        "evaluation_text": report,
        "next_eval_seconds": next_eval_seconds,
    }
