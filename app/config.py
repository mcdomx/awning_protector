import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, model_validator

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
CONFIG_PATH = DATA_DIR / "config.json"


class AutomationConfig(BaseModel):
    automation_enabled: bool = True
    max_wind_mph: float = 15.0
    sunny_lux_threshold: int = 10000
    sunny_wind_max_mph: float = 10.0
    deploy_duration_s: int = 3
    min_temp_c: float = 23.9  # ~75°F; always stored in Celsius
    temp_unit: str = "F"
    sunny_deploy_dwell_s: int = 60
    manual_override_min: int = 30
    rain_triggers_retract: bool = True
    wind_protection_enabled: bool = True
    sunny_deploy_enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _migrate_min_temp_f(cls, data: dict) -> dict:
        if isinstance(data, dict) and "min_temp_f" in data and "min_temp_c" not in data:
            data["min_temp_c"] = round((data.pop("min_temp_f") - 32.0) * 5.0 / 9.0, 1)
        return data


_config: Optional[AutomationConfig] = None


def load_config() -> AutomationConfig:
    global _config
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            _config = AutomationConfig(**json.load(f))
    else:
        _config = AutomationConfig()
        save_config(_config)
    return _config


def save_config(cfg: AutomationConfig) -> None:
    global _config
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg.model_dump(), f, indent=2)
    _config = cfg


def get_config() -> AutomationConfig:
    if _config is None:
        return load_config()
    return _config
