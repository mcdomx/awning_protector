import asyncio
from unittest.mock import patch

import pytest

from app.ai_agent import REQUIRED_OBS_FIELDS, AIEngine, _missing_obs_fields
from app.config import AIConfig, AutomationConfig


def make_complete_obs(**overrides):
    obs = {name: 1.0 for name in REQUIRED_OBS_FIELDS}
    obs.update(overrides)
    return obs


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
async def test_ai_engine_skips_pipeline_on_incomplete_weather_reading():
    cfg = AutomationConfig(ai=AIConfig(ai_enabled=True))
    engine = AIEngine()

    with patch("app.ai_agent.get_config", return_value=cfg), \
         patch("app.ai_agent.weather_client") as wc, \
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
         patch("app.ai_pipeline.run_ai_pipeline") as fake_pipeline:
        wc.latest_obs = make_complete_obs()
        fake_pipeline.return_value = {"evaluation_text": "ok", "next_eval_seconds": 1800}
        try:
            await asyncio.wait_for(engine.run(), timeout=0.2)
        except asyncio.TimeoutError:
            pass

    fake_pipeline.assert_called_once()
    assert engine.last_eval_text == "ok"
