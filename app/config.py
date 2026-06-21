import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
CONFIG_PATH = DATA_DIR / "config.json"


class AIConfig(BaseModel):
    ai_enabled: bool = False
    current_wind_threshold_mph: float = 4.0
    forecasted_wind_threshold_mph: float = 9.0
    earliest_auto_deployment: str = "8AM"
    latest_auto_deployment: str = "6PM"
    forecast_outlook_hours: int = 2
    max_deployment_seconds: int = 5
    min_deployment_seconds: int = 2
    min_deployment_temp_f: float = 65.0
    min_eval_interval_seconds: int = 300
    max_eval_interval_seconds: int = 4500


class AutomationConfig(BaseModel):
    automation_enabled: bool = True
    max_wind_mph: float = 15.0
    temp_unit: str = "F"
    manual_override_min: int = 30
    rain_triggers_retract: bool = True
    wind_protection_enabled: bool = True
    ai: AIConfig = Field(default_factory=AIConfig)


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


GUIDANCE_PATH = DATA_DIR / "guidance.json"


class UserGuidance(BaseModel):
    text: str
    expires_at: Optional[datetime] = None


_guidance: Optional[UserGuidance] = None


def load_guidance() -> Optional[UserGuidance]:
    global _guidance
    if GUIDANCE_PATH.exists():
        with open(GUIDANCE_PATH) as f:
            _guidance = UserGuidance(**json.load(f))
    else:
        _guidance = None
    return _guidance


def save_guidance(g: UserGuidance) -> None:
    global _guidance
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(GUIDANCE_PATH, "w") as f:
        json.dump(g.model_dump(mode="json"), f, indent=2)
    _guidance = g


def clear_guidance() -> None:
    global _guidance
    _guidance = None
    if GUIDANCE_PATH.exists():
        GUIDANCE_PATH.unlink()


def get_active_guidance_text() -> Optional[str]:
    global _guidance
    if _guidance is None:
        load_guidance()
    if _guidance is None or not _guidance.text:
        return None
    if _guidance.expires_at:
        tz = _guidance.expires_at.tzinfo or timezone.utc
        if datetime.now(tz) > _guidance.expires_at:
            return None
    return _guidance.text
