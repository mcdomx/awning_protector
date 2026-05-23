import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.automation import AutomationEngine
from app.config import AutomationConfig


def make_obs(**kwargs):
    defaults = {
        "precip_type": 0,
        "rain_prev_min_mm": 0.0,
        "wind_avg_m_s": 0.0,
        "illuminance_lux": 0,
        "uv_index": 0.0,
        "air_temp_c": 25.0,  # 25°C > default 23.9°C threshold
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
        sunny_lux_threshold=10000,
        sunny_wind_max_mph=10.0,
        deploy_duration_s=3,
        min_temp_c=23.9,
        sunny_deploy_dwell_s=0,  # no dwell for most tests
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
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_awaited_once()
    assert "rain" in engine.active_rule


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
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_awaited_once()
    assert "wind" in engine.active_rule


@pytest.mark.asyncio
async def test_sunny_calm_triggers_deploy(engine, cfg):
    obs = make_obs(illuminance_lux=15000, wind_avg_m_s=1.0, precip_type=0)
    awning = MagicMock()
    awning.current_state = "undeployed"
    awning.deploy = AsyncMock(return_value=True)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.forecast = []
        await engine._evaluate()

    awning.deploy.assert_awaited_once()
    assert engine._deployed_by_sunny is True
    assert engine._deploy_started_at is not None


@pytest.mark.asyncio
async def test_motor_stops_after_deploy_duration(engine, cfg):
    obs = make_obs(illuminance_lux=15000, wind_avg_m_s=1.0)
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.stop = AsyncMock(return_value=True)

    # Simulate motor already running, deploy duration elapsed
    engine._deployed_by_sunny = True
    engine._deploy_started_at = datetime.now(timezone.utc) - timedelta(seconds=cfg.deploy_duration_s + 1)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.forecast = []
        await engine._evaluate()

    awning.stop.assert_awaited_once()
    assert engine._deploy_started_at is None


@pytest.mark.asyncio
async def test_sunny_deploy_waits_for_dwell_time(engine):
    cfg = AutomationConfig(
        automation_enabled=True,
        sunny_deploy_dwell_s=60,
        sunny_lux_threshold=10000,
        sunny_wind_max_mph=10.0,
        min_temp_c=23.9,
    )
    obs = make_obs(illuminance_lux=15000, wind_avg_m_s=1.0)
    awning = MagicMock()
    awning.current_state = "undeployed"
    awning.deploy = AsyncMock(return_value=True)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.forecast = []
        await engine._evaluate()

    awning.deploy.assert_not_awaited()
    assert engine._sunny_conditions_met_since is not None
    assert "waiting" in engine.active_rule


@pytest.mark.asyncio
async def test_sunny_deploy_after_dwell_elapsed(engine):
    cfg = AutomationConfig(
        automation_enabled=True,
        sunny_deploy_dwell_s=60,
        sunny_lux_threshold=10000,
        sunny_wind_max_mph=10.0,
        min_temp_c=23.9,
    )
    obs = make_obs(illuminance_lux=15000, wind_avg_m_s=1.0)
    awning = MagicMock()
    awning.current_state = "undeployed"
    awning.deploy = AsyncMock(return_value=True)

    # Simulate dwell already elapsed
    engine._sunny_conditions_met_since = datetime.now(timezone.utc) - timedelta(seconds=61)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.forecast = []
        await engine._evaluate()

    awning.deploy.assert_awaited_once()


@pytest.mark.asyncio
async def test_sunny_deploy_blocked_by_low_temperature(engine, cfg):
    cfg.min_temp_c = 23.9
    obs = make_obs(illuminance_lux=15000, wind_avg_m_s=1.0, air_temp_c=20.0)  # 20°C < 23.9°C threshold
    awning = MagicMock()
    awning.current_state = "undeployed"
    awning.deploy = AsyncMock(return_value=True)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.forecast = []
        await engine._evaluate()

    awning.deploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_sunny_deploy_blocked_by_rain_forecast(engine, cfg):
    import time
    obs = make_obs(illuminance_lux=15000, wind_avg_m_s=1.0)
    awning = MagicMock()
    awning.current_state = "undeployed"
    awning.deploy = AsyncMock(return_value=True)

    # Forecast entry within 2 hours with 40% rain probability
    forecast = [{"dt": int(time.time()) + 3600, "pop": 0.4}]

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.forecast = forecast
        await engine._evaluate()

    awning.deploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_sunny_conditions_ended_retracts_awning(engine, cfg):
    obs = make_obs(illuminance_lux=500, wind_avg_m_s=1.0)  # lux dropped below threshold
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)

    engine._deployed_by_sunny = True

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_awaited_once()
    assert engine._deployed_by_sunny is False
    assert "retracting" in engine.active_rule


@pytest.mark.asyncio
async def test_sunny_conditions_ended_no_retract_if_not_deployed_by_sunny(engine, cfg):
    obs = make_obs(illuminance_lux=500, wind_avg_m_s=1.0)
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)

    engine._deployed_by_sunny = False  # manually deployed, not by sunny automation

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_rain_protection_clears_sunny_deploy_flag(engine, cfg):
    obs = make_obs(precip_type=1, rain_prev_min_mm=0.5)
    awning = MagicMock()
    awning.current_state = "deployed"
    awning.undeploy = AsyncMock(return_value=True)

    engine._deployed_by_sunny = True
    engine._sunny_conditions_met_since = datetime.now(timezone.utc)

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        wc.forecast = []
        await engine._evaluate()

    assert engine._deployed_by_sunny is False
    assert engine._sunny_conditions_met_since is None


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
        wc.forecast = []
        await engine._evaluate()

    awning.undeploy.assert_not_awaited()
