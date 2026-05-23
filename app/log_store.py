from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

MAX_ENTRIES = 5000
MPH_PER_MS = 2.23694


class AutomationLogEntry(BaseModel):
    id: int
    timestamp: str
    automation_name: str
    rule_description: str
    triggered: bool
    action_taken: Optional[str]


class WeatherLogEntry(BaseModel):
    id: int
    timestamp: str
    air_temp_c: Optional[float]
    wind_avg_mph: float
    wind_gust_mph: float
    wind_dir_deg: float
    precip_type: int
    rain_mm: float
    lux: int
    uv_index: float
    triggered_automation: Optional[str]
    triggered_action: Optional[str]
    trigger_field: Optional[str]
    trigger_reason: Optional[str]


class LogStore:
    def __init__(self) -> None:
        self._automation: deque = deque(maxlen=MAX_ENTRIES)
        self._weather: deque = deque(maxlen=MAX_ENTRIES)
        self._auto_id = 0
        self._weather_id = 0

    def add_automation(
        self,
        automation_name: str,
        rule_description: str,
        triggered: bool,
        action_taken: Optional[str] = None,
    ) -> None:
        self._auto_id += 1
        self._automation.append(AutomationLogEntry(
            id=self._auto_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            automation_name=automation_name,
            rule_description=rule_description,
            triggered=triggered,
            action_taken=action_taken,
        ))

    def add_weather(
        self,
        obs: Dict[str, Any],
        wind_avg_mph: float,
        triggered_automation: Optional[str] = None,
        triggered_action: Optional[str] = None,
        trigger_field: Optional[str] = None,
        trigger_reason: Optional[str] = None,
    ) -> None:
        self._weather_id += 1
        air_temp_c = obs.get("air_temp_c")
        self._weather.append(WeatherLogEntry(
            id=self._weather_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            air_temp_c=round(air_temp_c, 1) if air_temp_c is not None else None,
            wind_avg_mph=round(wind_avg_mph, 2),
            wind_gust_mph=round((obs.get("wind_gust_m_s") or 0.0) * MPH_PER_MS, 2),
            wind_dir_deg=obs.get("wind_direction_deg") or 0,
            precip_type=obs.get("precip_type") or 0,
            rain_mm=obs.get("rain_prev_min_mm") or 0.0,
            lux=obs.get("illuminance_lux") or 0,
            uv_index=obs.get("uv_index") or 0.0,
            triggered_automation=triggered_automation,
            triggered_action=triggered_action,
            trigger_field=trigger_field,
            trigger_reason=trigger_reason,
        ))

    def get_automation(self) -> List[AutomationLogEntry]:
        return list(reversed(self._automation))

    def get_weather(self) -> List[WeatherLogEntry]:
        return list(reversed(self._weather))


log_store = LogStore()
