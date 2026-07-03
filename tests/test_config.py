import json
import os
import tempfile
from pathlib import Path

import pytest

from datetime import datetime, timedelta, timezone

import app.config as cfg_module
from app.config import AutomationConfig, UserGuidance, get_active_presence, load_config, save_config, save_guidance


@pytest.fixture(autouse=True)
def tmp_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # reload module-level constants
    cfg_module.DATA_DIR = tmp_path
    cfg_module.CONFIG_PATH = tmp_path / "config.json"
    cfg_module.GUIDANCE_PATH = tmp_path / "guidance.json"
    cfg_module._config = None
    cfg_module._guidance = None
    yield
    cfg_module._config = None
    cfg_module._guidance = None


def test_load_defaults_when_no_file():
    cfg = load_config()
    assert cfg.automation_enabled is True
    assert cfg.max_wind_mph == 15.0
    assert cfg.temp_unit == "F"


def test_save_and_reload():
    original = load_config()
    original.max_wind_mph = 20.0
    save_config(original)

    cfg_module._config = None
    reloaded = load_config()
    assert reloaded.max_wind_mph == 20.0


def test_config_file_written():
    cfg = load_config()
    assert cfg_module.CONFIG_PATH.exists()
    with open(cfg_module.CONFIG_PATH) as f:
        data = json.load(f)
    assert "max_wind_mph" in data


def test_get_active_presence_none_when_no_guidance():
    assert get_active_presence() is None


def test_get_active_presence_returns_home_and_risk_tolerance():
    save_guidance(UserGuidance(text="I'm home", home=True, risk_tolerance=4))
    assert get_active_presence() == (True, 4)


def test_get_active_presence_none_when_expired():
    expired = datetime.now(timezone.utc) - timedelta(minutes=5)
    save_guidance(UserGuidance(text="away", expires_at=expired, home=False, risk_tolerance=5))
    assert get_active_presence() is None
