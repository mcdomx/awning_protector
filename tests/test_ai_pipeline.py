import json
from unittest.mock import patch

from app.ai_pipeline import (
    _RISKY,
    _gather_pipeline_context,
    _parse_json,
    _parse_next_eval,
    _skipped_worker,
)


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

        ctx = _gather_pipeline_context()

    assert ctx["current_obs"] == fake_obs
    assert ctx["forecast"] == fake_forecast
    assert ctx["awning_state"] == "deployed"
    assert ctx["history"] == fake_history
    assert "current_time" in ctx

    # Confirm both naming conventions are present so the wind-worker formatter's
    # `entry.get("wind_avg_m_s", entry.get("wind_avg", 0))` normalization applies.
    assert ctx["history"][0]["wind_avg_m_s"] == 1.0
    assert ctx["history"][1]["wind_avg"] == 2.0
