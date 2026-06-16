import asyncio
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.ai_agent import (
    REQUIRED_OBS_FIELDS,
    AIEngine,
    _missing_obs_fields,
    _next_window_open_at,
    _parse_deploy_hour,
    _within_deploy_window,
)
from app.config import AIConfig, AutomationConfig

_ET = ZoneInfo("America/New_York")


def make_complete_obs(**overrides):
    obs = {name: 1.0 for name in REQUIRED_OBS_FIELDS}
    obs.update(overrides)
    return obs


# ── _parse_deploy_hour ───────────────────────────────────────────────────────

def test_parse_deploy_hour_am():
    assert _parse_deploy_hour("8AM") == 8

def test_parse_deploy_hour_pm():
    assert _parse_deploy_hour("6PM") == 18

def test_parse_deploy_hour_noon():
    assert _parse_deploy_hour("12PM") == 12

def test_parse_deploy_hour_midnight():
    assert _parse_deploy_hour("12AM") == 0

def test_parse_deploy_hour_lowercase():
    assert _parse_deploy_hour("8am") == 8


# ── _within_deploy_window ─────────────────────────────────────────────────────

def test_within_deploy_window_inside():
    now = datetime(2026, 6, 13, 10, 0, tzinfo=_ET)   # 10 AM
    assert _within_deploy_window("8AM", "6PM", now=now) is True

def test_within_deploy_window_before_earliest():
    now = datetime(2026, 6, 13, 6, 0, tzinfo=_ET)    # 6 AM
    assert _within_deploy_window("8AM", "6PM", now=now) is False

def test_within_deploy_window_at_latest_is_outside():
    now = datetime(2026, 6, 13, 18, 0, tzinfo=_ET)   # exactly 6 PM
    assert _within_deploy_window("8AM", "6PM", now=now) is False

def test_within_deploy_window_at_earliest_is_inside():
    now = datetime(2026, 6, 13, 8, 0, tzinfo=_ET)    # exactly 8 AM
    assert _within_deploy_window("8AM", "6PM", now=now) is True

def test_within_deploy_window_after_latest():
    now = datetime(2026, 6, 13, 21, 0, tzinfo=_ET)   # 9 PM
    assert _within_deploy_window("8AM", "6PM", now=now) is False


# ── _next_window_open_at ──────────────────────────────────────────────────────

def test_next_window_open_at_before_window_today():
    now = datetime(2026, 6, 13, 5, 30, tzinfo=_ET)   # 5:30 AM — window not yet open
    result = _next_window_open_at("8AM", now=now)
    expected_local = datetime(2026, 6, 13, 8, 0, tzinfo=_ET)
    assert result == expected_local.astimezone(timezone.utc)

def test_next_window_open_at_after_window_tomorrow():
    now = datetime(2026, 6, 13, 20, 0, tzinfo=_ET)   # 8 PM — window already closed
    result = _next_window_open_at("8AM", now=now)
    expected_local = datetime(2026, 6, 14, 8, 0, tzinfo=_ET)
    assert result == expected_local.astimezone(timezone.utc)

def test_next_window_open_at_during_window_is_tomorrow():
    now = datetime(2026, 6, 13, 10, 0, tzinfo=_ET)   # 10 AM — window open, but we're rescheduling
    result = _next_window_open_at("8AM", now=now)
    expected_local = datetime(2026, 6, 14, 8, 0, tzinfo=_ET)
    assert result == expected_local.astimezone(timezone.utc)


# ── _missing_obs_fields ───────────────────────────────────────────────────────

def test_missing_obs_fields_empty_obs_reports_all_required():
    with patch("app.ai_agent.weather_client") as wc:
        wc.latest_obs = {}
        assert _missing_obs_fields() == list(REQUIRED_OBS_FIELDS)


def test_missing_obs_fields_reports_only_absent_or_none():
    with patch("app.ai_agent.weather_client") as wc:
        wc.latest_obs = make_complete_obs(uv_index=None)
        del wc.latest_obs["illuminance_lux"]
        missing = _missing_obs_fields()
        assert set(missing) == {"uv_index", "illuminance_lux"}


def test_missing_obs_fields_complete_obs_reports_nothing():
    with patch("app.ai_agent.weather_client") as wc:
        wc.latest_obs = make_complete_obs()
        assert _missing_obs_fields() == []


# ── AIEngine.run gating ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ai_engine_skips_pipeline_outside_deploy_window():
    cfg = AutomationConfig(ai=AIConfig(ai_enabled=True, earliest_auto_deployment="8AM", latest_auto_deployment="6PM"))
    engine = AIEngine()

    with patch("app.ai_agent.get_config", return_value=cfg), \
         patch("app.ai_agent._within_deploy_window", return_value=False), \
         patch("app.ai_agent._next_window_open_at", return_value=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)), \
         patch("app.ai_pipeline.run_ai_pipeline") as fake_pipeline:
        try:
            await asyncio.wait_for(engine.run(), timeout=0.2)
        except asyncio.TimeoutError:
            pass

    fake_pipeline.assert_not_called()
    assert not engine.is_running
    assert "Outside deployment window" in engine.last_eval_text
    assert engine.next_eval_at is not None


@pytest.mark.asyncio
async def test_ai_engine_skips_pipeline_on_incomplete_weather_reading():
    cfg = AutomationConfig(ai=AIConfig(ai_enabled=True))
    engine = AIEngine()

    with patch("app.ai_agent.get_config", return_value=cfg), \
         patch("app.ai_agent.weather_client") as wc, \
         patch("app.ai_agent._within_deploy_window", return_value=True), \
         patch("app.ai_pipeline.run_ai_pipeline") as fake_pipeline:
        wc.latest_obs = make_complete_obs(uv_index=None)  # incomplete
        try:
            await asyncio.wait_for(engine.run(), timeout=0.2)
        except asyncio.TimeoutError:
            pass

    fake_pipeline.assert_not_called()
    assert not engine.is_running
    assert "incomplete weather reading" in engine.last_eval_text
    assert "uv_index" in engine.last_eval_text
    assert engine.next_eval_at is not None


@pytest.mark.asyncio
async def test_ai_engine_runs_pipeline_when_weather_reading_complete():
    cfg = AutomationConfig(ai=AIConfig(ai_enabled=True))
    engine = AIEngine()

    with patch("app.ai_agent.get_config", return_value=cfg), \
         patch("app.ai_agent.weather_client") as wc, \
         patch("app.ai_agent._within_deploy_window", return_value=True), \
         patch("app.ai_pipeline.run_ai_pipeline") as fake_pipeline:
        wc.latest_obs = make_complete_obs()
        fake_pipeline.return_value = {"evaluation_text": "ok", "next_eval_seconds": 1800}
        try:
            await asyncio.wait_for(engine.run(), timeout=0.2)
        except asyncio.TimeoutError:
            pass

    fake_pipeline.assert_called_once()
    assert engine.last_eval_text == "ok"
