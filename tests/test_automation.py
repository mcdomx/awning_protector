import asyncio
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
        await engine._evaluate()

    awning.undeploy.assert_awaited_once()
    assert "wind" in engine.active_rule


@pytest.mark.asyncio
async def test_sunny_calm_triggers_deploy(engine, cfg):
    obs = make_obs(illuminance_lux=15000, wind_avg_m_s=1.0, precip_type=0)
    awning = MagicMock()
    awning.current_state = "undeployed"
    awning.deploy_timed = AsyncMock()

    with patch("app.automation.get_config", return_value=cfg), \
         patch("app.automation.weather_client") as wc, \
         patch("app.automation.awning_client", awning):
        wc.latest_obs = obs
        await engine._evaluate()

    awning.deploy_timed.assert_awaited_once_with(cfg.deploy_duration_s)


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
        await engine._evaluate()

    awning.undeploy.assert_not_awaited()
