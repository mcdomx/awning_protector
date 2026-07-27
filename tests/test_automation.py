import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.automation import WEATHER_RESTART_GRACE_S, AutomationEngine
from app.config import AutomationConfig


def make_obs(**kwargs):
    defaults = {
        "precip_type": 0,
        "rain_prev_min_mm": 0.0,
        "wind_avg_m_s": 0.0,
        "illuminance_lux": 0,
        "uv_index": 0.0,
        "air_temp_c": 25.0,
    }
    defaults.update(kwargs)
    return defaults


@pytest.fixture
def engine():
    return AutomationEngine()


@pytest.fixture
def cfg():
    return AutomationConfig(
        automation_enabled=True,
        max_wind_mph=15.0,
        manual_override_min=30,
        rain_triggers_retract=True,
    )


@pytest.mark.asyncio
async def test_rain_triggers_undeploy(engine, cfg):
    obs = make_obs(precip_type=1, rain_prev_min_mm=0.5)
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 5
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_awaited_once()
    assert "rain" in engine.active_rule


@pytest.mark.asyncio
async def test_wind_guard_logs_undeploy_with_reason(engine, cfg):
    # 7 m/s ≈ 15.66 mph > 15.0 threshold
    obs = make_obs(wind_avg_m_s=7.0)
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning), \
         patch("app.automation.log_store") as mock_log:
        wc.latest_obs = obs
        wc.latest_wind = {}
        wc.seconds_since_last_obs = 5
        wc.wait_for_wind_data = AsyncMock(side_effect=[None, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await engine._wind_guard()

    awning.undeploy.assert_awaited_once()
    mock_log.add_automation.assert_called_once()
    name, reason = mock_log.add_automation.call_args.args[:2]
    assert name == "wind_guard"
    assert "wind" in reason.lower()
    assert mock_log.add_automation.call_args.kwargs["action_taken"] == "undeploy"
    mock_log.add_weather.assert_called_once()
    assert mock_log.add_weather.call_args.kwargs["triggered_automation"] == "wind_guard"


@pytest.mark.asyncio
async def test_high_wind_triggers_undeploy(engine, cfg):
    # 7 m/s ≈ 15.66 mph > 15.0 threshold
    obs = make_obs(wind_avg_m_s=7.0)
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 5
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_awaited_once()
    assert "wind" in engine.active_rule


@pytest.mark.asyncio
async def test_manual_override_skips_rules(engine, cfg):
    engine.set_manual_override()
    obs = make_obs(precip_type=1)
    awning = MagicMock()
    awning.undeploy = AsyncMock()

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 5
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_automation_disabled_skips_rules(engine, cfg):
    cfg.automation_enabled = False
    obs = make_obs(precip_type=1)
    awning = MagicMock()
    awning.undeploy = AsyncMock()

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 5
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_mode_does_not_log_automation_entry(engine, cfg):
    cfg.ai.ai_enabled = True
    obs = make_obs(illuminance_lux=15000, wind_avg_m_s=1.0)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", MagicMock()), \
         patch("app.automation._within_deploy_window", return_value=True), \
         patch("app.automation.log_store") as mock_log:
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 5
        wc.forecast = []
        await engine._evaluate()

    mock_log.add_automation.assert_not_called()
    assert "AI mode" in engine.active_rule


@pytest.mark.asyncio
async def test_ai_deploy_window_closed_triggers_undeploy(engine, cfg):
    cfg.ai.ai_enabled = True
    obs = make_obs(illuminance_lux=15000, wind_avg_m_s=1.0)
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning), \
         patch("app.automation._within_deploy_window", return_value=False), \
         patch("app.automation.log_store") as mock_log:
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 5
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_awaited_once()
    assert "outside AI deploy window" in engine.active_rule
    mock_log.add_automation.assert_called_once()
    name, reason = mock_log.add_automation.call_args.args[:2]
    assert name == "deploy_window_closed"
    assert mock_log.add_automation.call_args.kwargs["action_taken"] == "undeploy"


@pytest.mark.asyncio
async def test_ai_deploy_window_closed_no_action_when_already_retracted(engine, cfg):
    cfg.ai.ai_enabled = True
    obs = make_obs(illuminance_lux=15000, wind_avg_m_s=1.0)
    awning = MagicMock()
    awning.current_state = "undeployed"
    awning.undeploy = AsyncMock(return_value=True)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning), \
         patch("app.automation._within_deploy_window", return_value=False):
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 5
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_weather_timeout_restarts_service_without_retracting(engine, cfg):
    obs = make_obs()
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning), \
         patch.object(engine, "_restart_weather_service", AsyncMock()) as restart_mock, \
         patch("app.automation.log_store") as mock_log:
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 301
        wc.forecast = []
        await engine._evaluate()

    restart_mock.assert_awaited_once()
    awning.undeploy.assert_not_awaited()
    mock_log.add_automation.assert_called_once()
    name = mock_log.add_automation.call_args.args[0]
    assert name == "weather_timeout_restart"
    assert engine._weather_restart_attempted_at is not None


@pytest.mark.asyncio
async def test_weather_timeout_waits_during_grace_period(engine, cfg):
    obs = make_obs()
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)
    engine._weather_restart_attempted_at = datetime.now(timezone.utc) - timedelta(
        seconds=WEATHER_RESTART_GRACE_S - 10
    )

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning), \
         patch.object(engine, "_restart_weather_service", AsyncMock()) as restart_mock, \
         patch("app.automation.log_store") as mock_log:
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 301
        wc.forecast = []
        await engine._evaluate()

    restart_mock.assert_not_awaited()
    awning.undeploy.assert_not_awaited()
    name = mock_log.add_automation.call_args.args[0]
    assert name == "weather_timeout_waiting"


@pytest.mark.asyncio
async def test_weather_timeout_retracts_after_grace_period_elapses(engine, cfg):
    obs = make_obs()
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)
    engine._weather_restart_attempted_at = datetime.now(timezone.utc) - timedelta(
        seconds=WEATHER_RESTART_GRACE_S + 10
    )

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning), \
         patch.object(engine, "_restart_weather_service", AsyncMock()) as restart_mock, \
         patch("app.automation.log_store") as mock_log:
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 301
        wc.forecast = []
        await engine._evaluate()

    restart_mock.assert_not_awaited()
    awning.undeploy.assert_awaited_once()
    name = mock_log.add_automation.call_args.args[0]
    assert name == "weather_timeout"
    assert engine._weather_timed_out is True


@pytest.mark.asyncio
async def test_weather_recovery_resets_restart_state(engine, cfg):
    obs = make_obs()
    engine._weather_timed_out = True
    engine._weather_restart_attempted_at = datetime.now(timezone.utc)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", MagicMock()):
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 5
        wc.forecast = []
        await engine._evaluate()

    assert engine._weather_restart_attempted_at is None
    assert engine._weather_timed_out is False


@pytest.mark.asyncio
async def test_ai_mode_rain_protection_still_fires(engine, cfg):
    cfg.ai.ai_enabled = True
    obs = make_obs(precip_type=1, rain_prev_min_mm=0.5)
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.seconds_since_last_obs = 5
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_awaited_once()
    assert "rain" in engine.active_rule
