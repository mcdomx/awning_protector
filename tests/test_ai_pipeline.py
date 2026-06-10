import json
import logging
from unittest.mock import patch

import pytest

from app.ai_pipeline import (
    MAX_TOOL_ITERATIONS,
    _RISKY,
    _gather_pipeline_context,
    _parse_json,
    _parse_next_eval,
    _run_worker,
    _skipped_worker,
    run_orchestrator,
)


# ── fakes for the _Claude interface ─────────────────────────────────────────────

class _Block:
    def __init__(self, type, text=None, name=None, input=None, id=None):
        self.type = type
        self.text = text
        self.name = name
        self.input = input or {}
        self.id = id


class _Msg:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _FakeClaude:
    """Minimal stand-in for _Claude that replays scripted chat responses."""

    def __init__(self, responses):
        self.model = "fake-model"
        self._responses = list(responses)

    def add_user_message(self, messages, message):
        messages.append({"role": "user", "content": message})

    def add_assistant_message(self, messages, message):
        messages.append({"role": "assistant", "content": message})

    def text_from_message(self, message):
        return "\n".join(b.text for b in message.content if b.type == "text")

    def chat(self, messages, **kwargs):
        if not self._responses:
            return _Msg([_Block("text", text="done")])
        return self._responses.pop(0)


# ── _parse_json ───────────────────────────────────────────────────────────────

def test_parse_json_valid():
    assessment, err = _parse_json('{"risk": "none", "reasoning": "calm"}', "wind")
    assert assessment == {"risk": "none", "reasoning": "calm"}
    assert err is None


def test_parse_json_strips_markdown_fences():
    raw = '```json\n{"risk": "low"}\n```'
    assessment, err = _parse_json(raw, "rain")
    assert assessment == {"risk": "low"}
    assert err is None


def test_parse_json_malformed():
    assessment, err = _parse_json('{"risk": "none"', "forecast")
    assert assessment == {}
    assert err is not None
    assert "forecast" in err


def test_parse_json_no_json_found():
    assessment, err = _parse_json("no json here", "solar")
    assert assessment == {}
    assert err is not None
    assert "solar" in err


# ── _parse_next_eval ──────────────────────────────────────────────────────────

def test_parse_next_eval_matches():
    report = "ORCHESTRATOR REPORT\n-------------------\nNext Eval In:     1800\n"
    assert _parse_next_eval(report, default=300) == 1800


def test_parse_next_eval_case_insensitive():
    report = "next eval in: 600"
    assert _parse_next_eval(report, default=300) == 600


def test_parse_next_eval_missing_returns_default():
    report = "ORCHESTRATOR REPORT\nAction Taken: none\n"
    assert _parse_next_eval(report, default=300) == 300


# ── fast-path predicate ───────────────────────────────────────────────────────

def test_fast_path_triggers_on_moderate_or_high_risk():
    for risk in ("moderate", "high"):
        assert risk in _RISKY
    for risk in ("low", "none"):
        assert risk not in _RISKY
    assert {}.get("risk") not in _RISKY  # missing key -> None, should not trigger fast-path


def test_skipped_worker_marks_assessment():
    result = _skipped_worker("forecast", "fast-path: skipped")
    assert result.name == "forecast"
    assert result.assessment == {"skipped": True, "reason": "fast-path: skipped"}


# ── _gather_pipeline_context ──────────────────────────────────────────────────

def test_gather_pipeline_context_assembles_live_data():
    fake_obs = {"air_temp_c": 30.0, "wind_avg_m_s": 1.5}
    fake_forecast = [{"dt": 1000, "pop": 0.1, "description": "clear", "wind_mph": 2.0, "temp_c": 28.0}]
    fake_history = [
        {"timestamp": "t1", "wind_avg_m_s": 1.0},
        {"timestamp": "t2", "wind_avg": 2.0},  # /weather/history naming quirk
    ]

    with patch("app.ai_pipeline.weather_client") as wc, \
         patch("app.ai_pipeline.awning_client") as ac, \
         patch("app.ai_pipeline.get_weather_history", return_value=json.dumps(fake_history)):
        wc.latest_obs = fake_obs
        wc.forecast = fake_forecast
        ac.current_state = "deployed"

        ctx = _gather_pipeline_context("task-test")

    assert ctx["current_obs"] == fake_obs
    assert ctx["forecast"] == fake_forecast
    assert ctx["awning_state"] == "deployed"
    assert ctx["history"] == fake_history
    assert "current_time" in ctx

    # Confirm both naming conventions are present so the wind-worker formatter's
    # `entry.get("wind_avg_m_s", entry.get("wind_avg", 0))` normalization applies.
    assert ctx["history"][0]["wind_avg_m_s"] == 1.0
    assert ctx["history"][1]["wind_avg"] == 2.0


# ── _run_worker error reporting ─────────────────────────────────────────────────

def test_run_worker_emits_parse_error_on_malformed_json(caplog):
    claude = _FakeClaude([_Msg([_Block("text", text='{"risk": "low"')])])  # malformed
    with caplog.at_level(logging.ERROR, logger="app.error_report"):
        result = _run_worker("wind", "msg", claude, [], "task-1")
    assert result.error is not None
    report = json.loads(caplog.records[-1].getMessage())
    assert report["error_code"] == "PARSE_ERROR"
    assert report["agent_id"] == "wind-worker"
    assert report["task_id"] == "task-1"


def test_run_worker_emits_validation_failed_when_risk_missing(caplog):
    claude = _FakeClaude([_Msg([_Block("text", text='{"reasoning": "no risk key"}')])])
    with caplog.at_level(logging.ERROR, logger="app.error_report"):
        _run_worker("rain", "msg", claude, [], "task-2")
    report = json.loads(caplog.records[-1].getMessage())
    assert report["error_code"] == "VALIDATION_FAILED"
    assert report["agent_id"] == "rain-worker"


def test_run_worker_no_report_on_valid_assessment(caplog):
    claude = _FakeClaude([_Msg([_Block("text", text='{"risk": "none", "reasoning": "calm"}')])])
    with caplog.at_level(logging.ERROR, logger="app.error_report"):
        result = _run_worker("solar", "msg", claude, [], "task-3")
    assert result.assessment == {"risk": "none", "reasoning": "calm"}
    assert caplog.records == []


def test_run_worker_emits_dependency_unavailable_on_api_failure(caplog):
    class _Boom(_FakeClaude):
        def chat(self, messages, **kwargs):
            raise RuntimeError("api down")

    claude = _Boom([])
    with caplog.at_level(logging.ERROR, logger="app.error_report"):
        with pytest.raises(RuntimeError):
            _run_worker("forecast", "msg", claude, [], "task-4")
    report = json.loads(caplog.records[-1].getMessage())
    assert report["error_code"] == "DEPENDENCY_UNAVAILABLE"
    assert report["agent_id"] == "forecast-worker"


# ── run_orchestrator hardening ──────────────────────────────────────────────────

def test_orchestrator_reports_tool_failure_and_completes(caplog):
    tool_use = _Msg(
        [_Block("tool_use", name="deploy_awning", input={"seconds": 3}, id="tu1")],
        stop_reason="tool_use",
    )
    final = _Msg([_Block("text", text="ORCHESTRATOR REPORT\nNext Eval In: 600")])
    claude = _FakeClaude([tool_use, final])

    with patch("app.ai_pipeline.execute_action_tool", side_effect=RuntimeError("service down")):
        with caplog.at_level(logging.ERROR, logger="app.error_report"):
            report_text = run_orchestrator("brief", claude, [], "task-5")

    assert "ORCHESTRATOR REPORT" in report_text  # run still completed
    report = json.loads(caplog.records[-1].getMessage())
    assert report["error_code"] == "DEPENDENCY_UNAVAILABLE"
    assert report["agent_id"] == "tool:deploy_awning"
    assert report["retry_eligible"] is False  # side-effecting tool
    assert report["suggested_action"] == "escalate"


def test_orchestrator_reports_max_iterations(caplog):
    # Always returns a tool_use block -> never reaches end_turn.
    looping = [
        _Msg([_Block("tool_use", name="get_awning_status", input={}, id=f"tu{i}")],
             stop_reason="tool_use")
        for i in range(MAX_TOOL_ITERATIONS + 2)
    ]
    claude = _FakeClaude(looping)

    with patch("app.ai_pipeline.execute_action_tool", return_value="retracted"):
        with caplog.at_level(logging.ERROR, logger="app.error_report"):
            run_orchestrator("brief", claude, [], "task-6")

    report = json.loads(caplog.records[-1].getMessage())
    assert report["error_code"] == "MAX_RETRIES_EXCEEDED"
    assert report["agent_id"] == "orchestrator"
    assert report["retry_eligible"] is False


# ── ai_tools action-tool hardening ──────────────────────────────────────────────

class _Resp:
    def __init__(self, ok):
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            import requests
            raise requests.HTTPError("500 Server Error")


def test_deploy_awning_raises_on_non_ok_response():
    from app import ai_tools
    import requests

    with patch("app.ai_tools.requests.get", return_value=_Resp(ok=False)):
        with pytest.raises(requests.HTTPError):
            ai_tools.deploy_awning(3)


def test_retract_awning_raises_on_non_ok_response():
    from app import ai_tools
    import requests

    with patch("app.ai_tools.requests.get", return_value=_Resp(ok=False)):
        with pytest.raises(requests.HTTPError):
            ai_tools.retract_awning()
