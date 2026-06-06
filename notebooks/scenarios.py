"""Synthetic test scenarios for the awning AI prompt sandbox.

Each scenario is a dict with:
  name, description, current_obs, history, forecast,
  awning_state, current_time, expected_action, objectives
"""
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List


def _ts(hour: int, minute: int = 0) -> str:
    now = datetime.now(timezone.utc)
    dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt.isoformat()


def _obs(
    air_temp_c: float,
    wind_avg_m_s: float,
    precip_type: int = 0,
    rain_prev_min_mm: float = 0.0,
    illuminance_lux: int = 0,
    uv_index: float = 0.0,
) -> Dict[str, Any]:
    return {
        "air_temp_c": air_temp_c,
        "wind_avg_m_s": wind_avg_m_s,
        "precip_type": precip_type,
        "rain_prev_min_mm": rain_prev_min_mm,
        "illuminance_lux": illuminance_lux,
        "uv_index": uv_index,
    }


def _history(base: Dict[str, Any], n: int = 6, wind_trend: List[float] = None) -> List[Dict]:
    """Generate n historical obs at 5-min intervals, optionally overriding wind_avg_m_s."""
    result = []
    now_ts = _time.time()
    for i in range(n):
        offset_s = (n - 1 - i) * 300
        entry = dict(base)
        entry["timestamp"] = datetime.fromtimestamp(now_ts - offset_s, tz=timezone.utc).isoformat()
        if wind_trend and i < len(wind_trend):
            entry["wind_avg_m_s"] = wind_trend[i]
        result.append(entry)
    return result


def _forecast(entries: List[Dict]) -> List[Dict]:
    """Build forecast list from (description, pop, wind_mph, temp_c) tuples with 1h offsets."""
    now_ts = int(_time.time())
    result = []
    for i, (desc, pop, wind_mph, temp_c) in enumerate(entries):
        result.append({
            "dt": now_ts + (i + 1) * 3600,
            "pop": pop,
            "description": desc,
            "wind_mph": wind_mph,
            "temp_c": temp_c,
        })
    return result


# ---------------------------------------------------------------------------
# Group A — Pure Protection (expected: retract)
# ---------------------------------------------------------------------------

_s1_obs = _obs(22.0, 1.0, precip_type=1, rain_prev_min_mm=2.5, illuminance_lux=2000, uv_index=1.0)
_s2_obs = _obs(28.0, 9.0, illuminance_lux=20000, uv_index=5.0)
_s3_obs = _obs(29.0, 0.9, illuminance_lux=35000, uv_index=6.0)
_s4_obs = _obs(27.0, 1.0, illuminance_lux=30000, uv_index=5.0)
_s5_obs = _obs(24.0, 0.6, illuminance_lux=0, uv_index=0.0)
_s6_obs = _obs(26.0, 0.8, precip_type=1, rain_prev_min_mm=0.2, illuminance_lux=10000, uv_index=2.0)
_s7_base = _obs(29.0, 2.9, illuminance_lux=25000, uv_index=5.0)
_s8_obs = _obs(28.0, 1.0, illuminance_lux=25000, uv_index=4.0)

SCENARIOS: List[Dict[str, Any]] = [
    {
        "name": "heavy_rain_deployed",
        "description": "Active rain with deployed awning — must retract immediately.",
        "current_obs": _s1_obs,
        "history": _history(_s1_obs),
        "forecast": _forecast([
            ("Rain", 0.9, 5.0, 22.0),
            ("Rain", 0.85, 5.0, 21.5),
            ("Cloudy", 0.5, 4.0, 21.0),
            ("Partly cloudy", 0.2, 3.0, 21.0),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(14),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "high_wind_deployed",
        "description": "Wind at ~20mph well above threshold with awning deployed.",
        "current_obs": _s2_obs,
        "history": _history(_s2_obs, wind_trend=[8.5, 8.7, 8.9, 9.0, 9.1, 9.0]),
        "forecast": _forecast([
            ("Partly cloudy", 0.05, 18.0, 28.0),
            ("Partly cloudy", 0.05, 17.0, 27.5),
            ("Sunny", 0.0, 15.0, 27.0),
            ("Sunny", 0.0, 12.0, 26.5),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(11),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "forecast_rain_1h",
        "description": "No current rain but 85% chance of rain in 1 hour — deployed awning.",
        "current_obs": _s3_obs,
        "history": _history(_s3_obs),
        "forecast": _forecast([
            ("Rain likely", 0.85, 8.0, 26.0),
            ("Heavy rain", 0.9, 10.0, 24.0),
            ("Rain", 0.7, 8.0, 23.5),
            ("Clearing", 0.3, 5.0, 23.0),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(13),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "forecast_high_wind_2h",
        "description": "Calm now but forecast shows 15mph wind in 2 hours — deployed awning.",
        "current_obs": _s4_obs,
        "history": _history(_s4_obs),
        "forecast": _forecast([
            ("Partly cloudy", 0.05, 6.0, 27.0),
            ("Windy", 0.1, 15.0, 26.5),
            ("Very windy", 0.15, 18.0, 26.0),
            ("Windy", 0.1, 14.0, 25.5),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(10),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "after_hours_deployed",
        "description": "9PM, well past the 7PM deployment window — awning is deployed.",
        "current_obs": _s5_obs,
        "history": _history(_s5_obs),
        "forecast": _forecast([
            ("Clear", 0.0, 3.0, 22.0),
            ("Clear", 0.0, 2.5, 21.0),
            ("Clear", 0.0, 2.0, 20.5),
            ("Clear", 0.0, 2.0, 20.0),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(21),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "light_mist_deployed",
        "description": "Light mist (0.2mm/min) — precipitation detected, awning deployed.",
        "current_obs": _s6_obs,
        "history": _history(_s6_obs),
        "forecast": _forecast([
            ("Drizzle", 0.7, 4.0, 25.5),
            ("Drizzle", 0.6, 3.5, 25.0),
            ("Partly cloudy", 0.3, 3.0, 25.0),
            ("Partly cloudy", 0.15, 3.0, 25.0),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(15),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "rising_wind_trend",
        "description": "Wind rising steadily to 2.9 m/s (~6.5mph) over 30 min — near threshold, deployed.",
        "current_obs": _s7_base,
        "history": _history(_s7_base, wind_trend=[0.5, 1.0, 1.5, 2.0, 2.5, 2.9]),
        "forecast": _forecast([
            ("Partly cloudy", 0.1, 7.0, 29.0),
            ("Partly cloudy", 0.1, 9.0, 28.5),
            ("Windy", 0.15, 12.0, 28.0),
            ("Windy", 0.2, 14.0, 27.5),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(14),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "forecast_rain_and_wind",
        "description": "70% rain + 12mph wind forecast in 1 hour — clear-cut protection case.",
        "current_obs": _s8_obs,
        "history": _history(_s8_obs),
        "forecast": _forecast([
            ("Thunderstorm likely", 0.7, 12.0, 25.0),
            ("Thunderstorm", 0.85, 15.0, 23.0),
            ("Heavy rain", 0.8, 12.0, 22.5),
            ("Rain", 0.5, 8.0, 22.0),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(12),
        "expected_action": "retract",
        "objectives": ["protection"],
    },

    # ---------------------------------------------------------------------------
    # Group B — Pure Solar Shielding (expected: deploy)
    # ---------------------------------------------------------------------------

    {
        "name": "peak_solar_calm",
        "description": "Noon, 35°C, max lux, near-zero wind — prime solar shielding opportunity.",
        "current_obs": _obs(35.0, 0.3, illuminance_lux=60000, uv_index=10.0),
        "history": _history(_obs(35.0, 0.3, illuminance_lux=60000, uv_index=10.0)),
        "forecast": _forecast([
            ("Sunny", 0.0, 2.0, 35.0),
            ("Sunny", 0.0, 2.0, 34.5),
            ("Sunny", 0.0, 2.5, 33.0),
            ("Sunny", 0.0, 2.5, 31.0),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(12),
        "expected_action": "deploy",
        "objectives": ["solar_shielding"],
    },
    {
        "name": "hot_sunny_morning",
        "description": "10AM, 30°C, lux=35000, calm — good morning solar shielding window.",
        "current_obs": _obs(30.0, 0.5, illuminance_lux=35000, uv_index=7.0),
        "history": _history(_obs(30.0, 0.5, illuminance_lux=35000, uv_index=7.0)),
        "forecast": _forecast([
            ("Sunny", 0.0, 3.0, 31.0),
            ("Sunny", 0.0, 3.0, 33.0),
            ("Sunny", 0.0, 3.5, 34.0),
            ("Sunny", 0.0, 3.0, 33.5),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(10),
        "expected_action": "deploy",
        "objectives": ["solar_shielding"],
    },
    {
        "name": "summer_afternoon",
        "description": "3PM, 32°C, lux=45000, light breeze — afternoon solar shielding.",
        "current_obs": _obs(32.0, 0.4, illuminance_lux=45000, uv_index=8.0),
        "history": _history(_obs(32.0, 0.4, illuminance_lux=45000, uv_index=8.0)),
        "forecast": _forecast([
            ("Sunny", 0.0, 3.0, 31.5),
            ("Sunny", 0.0, 3.5, 30.0),
            ("Partly cloudy", 0.05, 3.0, 28.0),
            ("Partly cloudy", 0.05, 3.0, 27.0),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(15),
        "expected_action": "deploy",
        "objectives": ["solar_shielding"],
    },
    {
        "name": "hot_stable_clear_forecast",
        "description": "1PM, 33°C, lux=50000, calm forecast 4h — clear deploy case.",
        "current_obs": _obs(33.0, 0.5, illuminance_lux=50000, uv_index=9.0),
        "history": _history(_obs(33.0, 0.5, illuminance_lux=50000, uv_index=9.0)),
        "forecast": _forecast([
            ("Sunny", 0.0, 2.5, 33.0),
            ("Sunny", 0.0, 2.5, 32.5),
            ("Sunny", 0.0, 3.0, 31.0),
            ("Sunny", 0.0, 3.0, 29.5),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(13),
        "expected_action": "deploy",
        "objectives": ["solar_shielding"],
    },
    {
        "name": "high_uv_hot_calm",
        "description": "Noon, 31°C, UV=9 (very high), lux=48000, near-zero wind.",
        "current_obs": _obs(31.0, 0.4, illuminance_lux=48000, uv_index=9.0),
        "history": _history(_obs(31.0, 0.4, illuminance_lux=48000, uv_index=9.0)),
        "forecast": _forecast([
            ("Sunny", 0.0, 2.0, 31.5),
            ("Sunny", 0.0, 2.5, 32.0),
            ("Sunny", 0.0, 2.5, 31.0),
            ("Partly cloudy", 0.05, 2.5, 29.0),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(12),
        "expected_action": "deploy",
        "objectives": ["solar_shielding"],
    },

    # ---------------------------------------------------------------------------
    # Group C — Conflicts: Protection vs. Solar Shielding
    # Scored on "protection" only — solar temptation must be resisted.
    # ---------------------------------------------------------------------------

    {
        "name": "hot_sunny_high_wind",
        "description": "34°C and lux=50000 (solar says deploy) but wind=9 m/s (~20mph) — protect.",
        "current_obs": _obs(34.0, 9.0, illuminance_lux=50000, uv_index=9.0),
        "history": _history(_obs(34.0, 9.0, illuminance_lux=50000, uv_index=9.0),
                            wind_trend=[8.5, 8.7, 9.0, 9.1, 9.0, 9.0]),
        "forecast": _forecast([
            ("Sunny", 0.0, 18.0, 34.0),
            ("Sunny", 0.0, 17.0, 33.5),
            ("Sunny", 0.0, 16.0, 33.0),
            ("Sunny", 0.0, 14.0, 32.0),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(13),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "hot_sunny_active_rain",
        "description": "30°C and lux=15000 (solar benefit) but active rain — no deploy.",
        "current_obs": _obs(30.0, 0.9, precip_type=1, rain_prev_min_mm=1.5,
                            illuminance_lux=15000, uv_index=3.0),
        "history": _history(_obs(30.0, 0.9, precip_type=1, rain_prev_min_mm=1.5,
                                  illuminance_lux=15000, uv_index=3.0)),
        "forecast": _forecast([
            ("Rain showers", 0.75, 5.0, 29.0),
            ("Partly cloudy", 0.3, 4.0, 28.5),
            ("Sunny", 0.05, 3.0, 29.0),
            ("Sunny", 0.0, 3.0, 29.5),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(13),
        "expected_action": "none",
        "objectives": ["protection"],
    },
    {
        "name": "hot_sunny_forecast_rain_1h",
        "description": "33°C, lux=45000, perfect solar conditions — but 85% rain in 1h. Retract.",
        "current_obs": _obs(33.0, 0.8, illuminance_lux=45000, uv_index=8.0),
        "history": _history(_obs(33.0, 0.8, illuminance_lux=45000, uv_index=8.0)),
        "forecast": _forecast([
            ("Rain likely", 0.85, 8.0, 28.0),
            ("Heavy rain", 0.9, 10.0, 25.0),
            ("Rain", 0.6, 6.0, 24.5),
            ("Clearing", 0.25, 4.0, 24.0),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(11),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "extreme_heat_near_wind_threshold",
        "description": "38°C, lux=55000 — extreme heat demands shielding. Wind=1.35 m/s (~3.0mph = threshold). Do not deploy.",
        "current_obs": _obs(38.0, 1.35, illuminance_lux=55000, uv_index=10.0),
        "history": _history(_obs(38.0, 1.35, illuminance_lux=55000, uv_index=10.0),
                            wind_trend=[1.1, 1.2, 1.3, 1.35, 1.35, 1.35]),
        "forecast": _forecast([
            ("Sunny", 0.0, 3.5, 38.5),
            ("Sunny", 0.0, 3.8, 38.0),
            ("Sunny", 0.0, 4.0, 37.0),
            ("Sunny", 0.0, 3.5, 35.0),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(14),
        "expected_action": "none",
        "objectives": ["protection"],
    },
    {
        "name": "hot_sunny_approaching_storm",
        "description": "32°C, lux=40000 — currently great. Forecast: wind 20mph + 90% rain in 2h. Retract.",
        "current_obs": _obs(32.0, 0.7, illuminance_lux=40000, uv_index=8.0),
        "history": _history(_obs(32.0, 0.7, illuminance_lux=40000, uv_index=8.0)),
        "forecast": _forecast([
            ("Partly cloudy", 0.2, 8.0, 30.0),
            ("Storm approaching", 0.9, 20.0, 26.0),
            ("Thunderstorm", 0.95, 22.0, 24.0),
            ("Heavy rain", 0.85, 15.0, 23.0),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(11),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "blistering_heat_forecast_storm",
        "description": "40°C scorching noon, lux=58000 — extreme solar. Forecast: 25mph wind + heavy rain in 2h. Retract.",
        "current_obs": _obs(40.0, 0.5, illuminance_lux=58000, uv_index=11.0),
        "history": _history(_obs(40.0, 0.5, illuminance_lux=58000, uv_index=11.0)),
        "forecast": _forecast([
            ("Sunny, hot", 0.1, 10.0, 39.0),
            ("Storm developing", 0.85, 25.0, 30.0),
            ("Severe storm", 0.95, 28.0, 25.0),
            ("Heavy rain", 0.9, 20.0, 24.0),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(12),
        "expected_action": "retract",
        "objectives": ["protection"],
    },
    {
        "name": "evening_hot_near_cutoff",
        "description": "6:45PM (15 min before 7PM cutoff), 34°C, lux=30000, undeployed. Too late to deploy.",
        "current_obs": _obs(34.0, 0.6, illuminance_lux=30000, uv_index=5.0),
        "history": _history(_obs(34.0, 0.6, illuminance_lux=30000, uv_index=5.0)),
        "forecast": _forecast([
            ("Sunny", 0.0, 3.0, 33.0),
            ("Partly cloudy", 0.0, 3.0, 30.0),
            ("Clear", 0.0, 2.5, 28.0),
            ("Clear", 0.0, 2.5, 26.0),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(18, 45),
        "expected_action": "none",
        "objectives": ["protection"],
    },
    {
        "name": "borderline_rain_forecast",
        "description": "32°C, lux=42000, deployed. Forecast: 35% rain in exactly 2h (just above 30% threshold). Retract.",
        "current_obs": _obs(32.0, 0.8, illuminance_lux=42000, uv_index=7.0),
        "history": _history(_obs(32.0, 0.8, illuminance_lux=42000, uv_index=7.0)),
        "forecast": _forecast([
            ("Partly cloudy", 0.15, 4.0, 31.0),
            ("Increasing clouds", 0.35, 5.0, 29.5),
            ("Rain possible", 0.45, 6.0, 28.0),
            ("Clearing", 0.2, 4.0, 27.5),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(13),
        "expected_action": "retract",
        "objectives": ["protection"],
    },

    # ---------------------------------------------------------------------------
    # Group D — No-Action / Ambiguous (expected: none)
    # ---------------------------------------------------------------------------

    {
        "name": "before_deploy_window",
        "description": "5AM — before 8AM deployment window. Perfect conditions but too early.",
        "current_obs": _obs(28.0, 0.5, illuminance_lux=1000, uv_index=0.5),
        "history": _history(_obs(28.0, 0.5, illuminance_lux=1000, uv_index=0.5)),
        "forecast": _forecast([
            ("Sunny", 0.0, 2.0, 29.0),
            ("Sunny", 0.0, 2.5, 31.0),
            ("Sunny", 0.0, 2.5, 33.0),
            ("Sunny", 0.0, 2.5, 34.0),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(5),
        "expected_action": "none",
        "objectives": ["token_efficiency"],
    },
    {
        "name": "cold_but_sunny",
        "description": "15°C (59°F < 65°F temp threshold), lux=40000 — sunny but too cold to deploy.",
        "current_obs": _obs(15.0, 0.5, illuminance_lux=40000, uv_index=6.0),
        "history": _history(_obs(15.0, 0.5, illuminance_lux=40000, uv_index=6.0)),
        "forecast": _forecast([
            ("Sunny", 0.0, 2.5, 16.0),
            ("Sunny", 0.0, 3.0, 17.0),
            ("Sunny", 0.0, 3.0, 17.5),
            ("Sunny", 0.0, 2.5, 16.5),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(13),
        "expected_action": "none",
        "objectives": ["solar_shielding", "token_efficiency"],
    },
    {
        "name": "overcast_mild",
        "description": "24°C, lux=3000 (below 10000 threshold), no rain, calm — no solar benefit.",
        "current_obs": _obs(24.0, 0.8, illuminance_lux=3000, uv_index=1.0),
        "history": _history(_obs(24.0, 0.8, illuminance_lux=3000, uv_index=1.0)),
        "forecast": _forecast([
            ("Overcast", 0.1, 4.0, 24.0),
            ("Overcast", 0.1, 4.0, 23.5),
            ("Cloudy", 0.15, 3.5, 23.0),
            ("Partly cloudy", 0.1, 3.0, 23.0),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(12),
        "expected_action": "none",
        "objectives": ["token_efficiency"],
    },
    {
        "name": "stable_deployed_safe",
        "description": "30°C, lux=35000, deployed, calm forecast 4h — maintain, no action needed.",
        "current_obs": _obs(30.0, 0.8, illuminance_lux=35000, uv_index=7.0),
        "history": _history(_obs(30.0, 0.8, illuminance_lux=35000, uv_index=7.0)),
        "forecast": _forecast([
            ("Sunny", 0.0, 3.0, 30.0),
            ("Sunny", 0.0, 3.0, 29.5),
            ("Sunny", 0.0, 3.5, 28.5),
            ("Partly cloudy", 0.05, 3.0, 27.5),
        ]),
        "awning_state": "deployed",
        "current_time": _ts(14),
        "expected_action": "none",
        "objectives": ["solar_shielding", "token_efficiency"],
    },
    {
        "name": "marginal_wind_hot",
        "description": "28°C, lux=38000, wind=1.35 m/s (~3.0mph = current threshold), undeployed — do not deploy.",
        "current_obs": _obs(28.0, 1.35, illuminance_lux=38000, uv_index=7.0),
        "history": _history(_obs(28.0, 1.35, illuminance_lux=38000, uv_index=7.0)),
        "forecast": _forecast([
            ("Sunny", 0.0, 3.5, 28.0),
            ("Sunny", 0.0, 4.0, 28.5),
            ("Sunny", 0.05, 4.0, 28.0),
            ("Partly cloudy", 0.05, 3.5, 27.5),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(14),
        "expected_action": "none",
        "objectives": ["protection", "token_efficiency"],
    },
    {
        "name": "low_lux_warm",
        "description": "26°C, lux=6000 (below 10000 threshold), no rain, calm — not sunny enough.",
        "current_obs": _obs(26.0, 0.7, illuminance_lux=6000, uv_index=2.0),
        "history": _history(_obs(26.0, 0.7, illuminance_lux=6000, uv_index=2.0)),
        "forecast": _forecast([
            ("Partly cloudy", 0.05, 3.0, 26.5),
            ("Partly cloudy", 0.05, 3.0, 27.0),
            ("Partly cloudy", 0.1, 3.5, 27.0),
            ("Cloudy", 0.15, 3.5, 26.5),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(11),
        "expected_action": "none",
        "objectives": ["token_efficiency"],
    },
    {
        "name": "cold_borderline_temp",
        "description": "18°C (64.4°F, just below 65°F threshold), lux=25000 — temp constraint blocks deploy.",
        "current_obs": _obs(18.0, 0.5, illuminance_lux=25000, uv_index=5.0),
        "history": _history(_obs(18.0, 0.5, illuminance_lux=25000, uv_index=5.0)),
        "forecast": _forecast([
            ("Sunny", 0.0, 2.5, 18.5),
            ("Sunny", 0.0, 3.0, 19.0),
            ("Sunny", 0.0, 3.0, 19.0),
            ("Partly cloudy", 0.05, 3.0, 18.5),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(13),
        "expected_action": "none",
        "objectives": ["solar_shielding", "token_efficiency"],
    },
    {
        "name": "just_after_rain_cleared",
        "description": "Rain cleared 10min ago (history shows rain, current is clear), 30°C, lux=28000, 2PM. Hold — recent rain, caution.",
        "current_obs": _obs(30.0, 0.9, precip_type=0, rain_prev_min_mm=0.0,
                            illuminance_lux=28000, uv_index=6.0),
        "history": [
            {**_obs(28.0, 1.0, precip_type=1, rain_prev_min_mm=1.5, illuminance_lux=5000),
             "timestamp": datetime.fromtimestamp(_time.time() - 1500, tz=timezone.utc).isoformat()},
            {**_obs(28.5, 1.0, precip_type=1, rain_prev_min_mm=0.8, illuminance_lux=8000),
             "timestamp": datetime.fromtimestamp(_time.time() - 1200, tz=timezone.utc).isoformat()},
            {**_obs(29.0, 0.9, precip_type=1, rain_prev_min_mm=0.3, illuminance_lux=12000),
             "timestamp": datetime.fromtimestamp(_time.time() - 900, tz=timezone.utc).isoformat()},
            {**_obs(29.5, 0.9, precip_type=1, rain_prev_min_mm=0.1, illuminance_lux=18000),
             "timestamp": datetime.fromtimestamp(_time.time() - 600, tz=timezone.utc).isoformat()},
            {**_obs(30.0, 0.9, precip_type=0, rain_prev_min_mm=0.0, illuminance_lux=22000),
             "timestamp": datetime.fromtimestamp(_time.time() - 300, tz=timezone.utc).isoformat()},
            {**_obs(30.0, 0.9, precip_type=0, rain_prev_min_mm=0.0, illuminance_lux=28000),
             "timestamp": datetime.fromtimestamp(_time.time(), tz=timezone.utc).isoformat()},
        ],
        "forecast": _forecast([
            ("Partly cloudy", 0.15, 3.0, 30.0),
            ("Sunny", 0.05, 3.0, 30.5),
            ("Sunny", 0.0, 3.0, 30.0),
            ("Sunny", 0.0, 3.0, 29.5),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(14),
        "expected_action": "none",
        "objectives": ["protection", "solar_shielding", "token_efficiency"],
    },
    {
        "name": "night_undeployed_clear",
        "description": "11PM, 22°C, lux=0, clear overnight — awning already retracted, nothing to do.",
        "current_obs": _obs(22.0, 0.5, illuminance_lux=0, uv_index=0.0),
        "history": _history(_obs(22.0, 0.5, illuminance_lux=0, uv_index=0.0)),
        "forecast": _forecast([
            ("Clear", 0.0, 2.0, 21.0),
            ("Clear", 0.0, 2.0, 20.5),
            ("Clear", 0.0, 1.5, 20.0),
            ("Clear", 0.0, 1.5, 19.5),
        ]),
        "awning_state": "undeployed",
        "current_time": _ts(23),
        "expected_action": "none",
        "objectives": ["token_efficiency"],
    },
]

assert len(SCENARIOS) == 30, f"Expected 30 scenarios, got {len(SCENARIOS)}"
SCENARIOS_BY_NAME = {s["name"]: s for s in SCENARIOS}
