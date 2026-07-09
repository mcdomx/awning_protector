import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from .ai_agent import _Claude, _build_system_blocks
from .ai_tools import (
    action_tool_schemas, execute_action_tool, get_weather_history, glare_deploy_tool_schema,
)
from .automation import MPH_PER_MS
from .awning import awning_client
from .config import get_active_guidance_text
from .error_report import (
    DEPENDENCY_UNAVAILABLE,
    MAX_RETRIES_EXCEEDED,
    PARSE_ERROR,
    TOOL_NOT_FOUND,
    VALIDATION_FAILED,
    emit_error_report,
)
from .uv_sensor import uv_sensor_client
from .weather import weather_client

_C_TO_F = lambda c: round(c * 9 / 5 + 32, 1)

# Upper bound on orchestrator tool-use iterations before we give up on a run.
MAX_TOOL_ITERATIONS = 8

# Tools that change physical/awning state — a failure after dispatching one is not
# safely retry-eligible because it may have partially taken effect.
_SIDE_EFFECTING_TOOLS = {"deploy_awning", "retract_awning", "deploy_for_glare"}

# The awning's physical location (and the earliest/latest_auto_deployment clock
# strings such as "8AM"/"6PM" in AIConfig) are local US/Eastern time, while the
# weather service reports in UTC. All times shown to the LLM are normalized to
# this zone and labeled so the model can compare them directly.
_LOCAL_TZ = ZoneInfo("America/New_York")

_RISKY = {"moderate", "high"}

# UV sensor is an optional, best-effort data source — treat a reading older than this as
# unavailable and skip the glare worker rather than block or fail the pipeline.
GLARE_STALE_SECONDS = 30


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


def _glare_stale() -> bool:
    staleness = uv_sensor_client.seconds_since_last_reading
    return staleness is None or staleness > GLARE_STALE_SECONDS


def _gather_pipeline_context(task_id: str) -> Dict[str, Any]:
    try:
        history = json.loads(get_weather_history(minutes=60))
        return {
            "current_obs": weather_client.latest_obs,
            "history": history,
            "forecast": weather_client.forecast,
            "awning_state": awning_client.current_state,
            "current_time": datetime.now(_LOCAL_TZ).strftime("%-I:%M %p %Z, %a %b %d"),
            "glare_obs": uv_sensor_client.latest_reading,
            "glare_stale": _glare_stale(),
        }
    except Exception as exc:
        emit_error_report(
            error_code=DEPENDENCY_UNAVAILABLE,
            message=f"Failed to gather pipeline context (weather data): {exc}",
            task_id=task_id,
            agent_id="pipeline-context",
            input_snapshot={},
            retry_eligible=True,
            suggested_action="retry",
        )
        raise


def _run_worker(
    name: str, user_msg: str, claude: _Claude, system_blocks: list, task_id: str
) -> WorkerResult:
    """Shared worker body: call Claude, parse the JSON assessment, and emit a
    structured error report on an API failure or an invalid/missing assessment."""
    msgs = []
    claude.add_user_message(msgs, user_msg)
    try:
        resp = claude.chat(msgs, system_blocks=system_blocks, temperature=0)
    except Exception as exc:
        emit_error_report(
            error_code=DEPENDENCY_UNAVAILABLE,
            message=f"Claude API call failed for {name} worker: {exc}",
            task_id=task_id,
            agent_id=f"{name}-worker",
            input_snapshot={"user_msg": user_msg, "model": claude.model},
            retry_eligible=True,
            suggested_action="retry",
        )
        raise

    raw = claude.text_from_message(resp)
    assessment, err = _parse_json(raw, name)
    if err or "risk" not in assessment:
        emit_error_report(
            error_code=PARSE_ERROR if err else VALIDATION_FAILED,
            message=err or f"{name} worker response missing required 'risk' field",
            task_id=task_id,
            agent_id=f"{name}-worker",
            input_snapshot={"raw_response": raw},
            retry_eligible=True,
            suggested_action="retry",
        )
    return WorkerResult(name, assessment, raw, err)


# ── Worker functions ──────────────────────────────────────────────────────────

def run_wind_worker(ctx: Dict, claude: _Claude, system_blocks: list, task_id: str) -> WorkerResult:
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

    return _run_worker("wind", user_msg, claude, system_blocks, task_id)


def run_rain_worker(ctx: Dict, claude: _Claude, system_blocks: list, task_id: str) -> WorkerResult:
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

    return _run_worker("rain", user_msg, claude, system_blocks, task_id)


def run_forecast_worker(ctx: Dict, claude: _Claude, system_blocks: list, task_id: str) -> WorkerResult:
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
        lines.append(f"  {ts}  conditions={desc}  precip_prob={pop:.0%}  wind_avg={w}mph  air_temp={tc}C")
    user_msg = "\n".join(lines)

    return _run_worker("forecast", user_msg, claude, system_blocks, task_id)


def run_solar_worker(ctx: Dict, claude: _Claude, system_blocks: list, task_id: str) -> WorkerResult:
    obs = ctx["current_obs"]
    temp_f = _C_TO_F(obs.get("air_temp_c", 0.0))
    user_msg = "\n".join([
        f"Current time  : {ctx['current_time']}",
        f"Temperature   : {obs.get('air_temp_c')}°C  ({temp_f}°F)",
        f"Illuminance   : {obs.get('illuminance_lux')} lux",
        f"UV index      : {obs.get('uv_index')}",
    ])

    return _run_worker("solar", user_msg, claude, system_blocks, task_id)


def run_glare_worker(ctx: Dict, claude: _Claude, system_blocks: list, task_id: str) -> WorkerResult:
    obs = ctx.get("glare_obs", {})
    user_msg = "\n".join([
        f"Current time  : {ctx['current_time']}",
        f"Illuminance   : {obs.get('illuminance_lux')} lux",
        f"UV index      : {obs.get('uv_index')}",
    ])

    return _run_worker("glare", user_msg, claude, system_blocks, task_id)


# ── Coordinator ───────────────────────────────────────────────────────────────

def run_coordinator(
    wind: WorkerResult, rain: WorkerResult,
    forecast: WorkerResult, solar: WorkerResult, glare: WorkerResult,
    ctx: Dict, claude: _Claude, system_blocks: list, task_id: str,
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
        "",
        "GLARE WORKER:",
        json.dumps(glare.assessment, indent=2),
    ])
    msgs = []
    claude.add_user_message(msgs, user_msg)
    try:
        resp = claude.chat(msgs, system_blocks=system_blocks, temperature=0)
    except Exception as exc:
        emit_error_report(
            error_code=DEPENDENCY_UNAVAILABLE,
            message=f"Claude API call failed in coordinator: {exc}",
            task_id=task_id,
            agent_id="coordinator",
            input_snapshot={"user_msg": user_msg, "model": claude.model},
            retry_eligible=True,
            suggested_action="retry",
        )
        raise
    return claude.text_from_message(resp)


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_orchestrator(
    brief: str, claude: _Claude, system_blocks: list, task_id: str, tools: list = action_tool_schemas,
) -> str:
    msgs = []
    claude.add_user_message(msgs, brief)

    resp = None
    for _ in range(MAX_TOOL_ITERATIONS):
        try:
            resp = claude.chat(
                msgs,
                system_blocks=system_blocks,
                temperature=0,
                tools=tools,
                streaming=True,
            )
        except Exception as exc:
            emit_error_report(
                error_code=DEPENDENCY_UNAVAILABLE,
                message=f"Claude API call failed in orchestrator: {exc}",
                task_id=task_id,
                agent_id="orchestrator",
                input_snapshot={"brief": brief, "model": claude.model},
                retry_eligible=True,
                suggested_action="retry",
            )
            raise
        claude.add_assistant_message(msgs, resp)

        if resp.stop_reason == "end_turn":
            return claude.text_from_message(resp)

        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                try:
                    result = execute_action_tool(block.name, block.input)
                except Exception as exc:
                    side_effecting = block.name in _SIDE_EFFECTING_TOOLS
                    emit_error_report(
                        error_code=TOOL_NOT_FOUND if isinstance(exc, ValueError) else DEPENDENCY_UNAVAILABLE,
                        message=f"Action tool '{block.name}' failed: {exc}",
                        task_id=task_id,
                        agent_id=f"tool:{block.name}",
                        input_snapshot=dict(block.input),
                        retry_eligible=not side_effecting,
                        suggested_action="escalate" if side_effecting else "retry",
                    )
                    result = f"ERROR: tool '{block.name}' failed: {exc}"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        if not tool_results:
            return claude.text_from_message(resp)
        msgs.append({"role": "user", "content": tool_results})

    emit_error_report(
        error_code=MAX_RETRIES_EXCEEDED,
        message=f"Orchestrator exceeded {MAX_TOOL_ITERATIONS} tool iterations without completing.",
        task_id=task_id,
        agent_id="orchestrator",
        input_snapshot={"brief": brief},
        retry_eligible=False,
        suggested_action="escalate",
    )
    return claude.text_from_message(resp)


# ── Pipeline entry point ──────────────────────────────────────────────────────

def run_ai_pipeline(cfg) -> dict:
    task_id = uuid.uuid4().hex
    model = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
    claude = _Claude(model=model)
    ctx = _gather_pipeline_context(task_id)

    wind_blocks = _build_system_blocks("wind", cfg)
    rain_blocks = _build_system_blocks("rain", cfg)
    forecast_blocks = _build_system_blocks("forecast", cfg)
    solar_blocks = _build_system_blocks("solar", cfg)
    glare_blocks = _build_system_blocks("glare", cfg)
    coordinator_blocks = _build_system_blocks("coordinator", cfg)
    orchestrator_blocks = _build_system_blocks(
        "orchestrator",
        cfg,
        min_eval_interval=cfg.min_eval_interval_seconds // 60,
        max_eval_interval=cfg.max_eval_interval_seconds // 60,
    )
    user_guidance = get_active_guidance_text()
    if user_guidance:
        orchestrator_blocks.append({
            "type": "text",
            "text": (
                "<user_guidance>\n"
                "The following context has been provided by the user. It does not override "
                "the hard rules above, but should inform your risk tolerance and evaluation "
                "frequency within the space those rules allow:\n\n"
                f"{user_guidance}\n"
                "</user_guidance>\n\n"
                "Because user guidance is active, add this line to your ORCHESTRATOR REPORT "
                "(after Solar Benefit, before Primary Reason):\n"
                "User Guidance:    <one sentence describing how the guidance influenced this decision>"
            ),
            "cache_control": {"type": "ephemeral"},
        })

    wind_r = run_wind_worker(ctx, claude, wind_blocks, task_id)
    rain_r = run_rain_worker(ctx, claude, rain_blocks, task_id)

    fast_path = (
        wind_r.assessment.get("risk") in _RISKY
        or rain_r.assessment.get("risk") in _RISKY
    )

    if fast_path:
        reason = "fast-path: wind or rain worker reported moderate/high risk"
        forecast_r = _skipped_worker("forecast", reason)
        solar_r = _skipped_worker("solar", reason)
        glare_r = _skipped_worker("glare", reason)
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
        forecast_r = run_forecast_worker(ctx, claude, forecast_blocks, task_id)
        solar_r = run_solar_worker(ctx, claude, solar_blocks, task_id)
        glare_r = (
            _skipped_worker("glare", "uv sensor unavailable")
            if ctx["glare_stale"]
            else run_glare_worker(ctx, claude, glare_blocks, task_id)
        )
        brief = run_coordinator(
            wind_r, rain_r, forecast_r, solar_r, glare_r,
            ctx, claude, coordinator_blocks, task_id,
        )

    glare_eligible = (
        not fast_path
        and not ctx["glare_stale"]
        and glare_r.assessment.get("glare_detected") is True
        and wind_r.assessment.get("risk") == "none"
        and rain_r.assessment.get("risk") == "none"
    )
    tools = action_tool_schemas + ([glare_deploy_tool_schema] if glare_eligible else [])

    report = run_orchestrator(brief, claude, orchestrator_blocks, task_id, tools=tools)
    next_eval_seconds = _parse_next_eval(report, cfg.min_eval_interval_seconds)
    if not re.search(r"Next Eval In:\s*(\d+)", report, re.IGNORECASE):
        emit_error_report(
            error_code=VALIDATION_FAILED,
            message="Orchestrator report missing 'Next Eval In:' line; using default interval.",
            task_id=task_id,
            agent_id="orchestrator",
            input_snapshot={"report": report},
            retry_eligible=True,
            suggested_action="skip",
        )

    return {
        "evaluation_text": report,
        "next_eval_seconds": next_eval_seconds,
    }
