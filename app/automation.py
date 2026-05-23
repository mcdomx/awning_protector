import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .awning import awning_client
from .config import get_config
from .log_store import log_store
from .weather import weather_client

logger = logging.getLogger(__name__)

MPH_PER_MS = 2.23694
EVAL_INTERVAL_S = 10
RAIN_FORECAST_POP_THRESHOLD = 0.3
RAIN_FORECAST_WINDOW_S = 7200  # 2 hours
WEATHER_TIMEOUT_S = 120  # retract after 2 minutes with no obs_st


class AutomationEngine:
    def __init__(self) -> None:
        self._override_until: Optional[datetime] = None
        self._last_action: Optional[str] = None
        self._active_rule: Optional[str] = None
        self._sunny_conditions_met_since: Optional[datetime] = None
        self._deployed_by_sunny: bool = False
        self._deploy_started_at: Optional[datetime] = None

    def set_manual_override(self) -> None:
        cfg = get_config()
        self._override_until = datetime.now(timezone.utc) + timedelta(minutes=cfg.manual_override_min)
        self._active_rule = "manual override"
        self._deployed_by_sunny = False
        self._sunny_conditions_met_since = None
        self._deploy_started_at = None

    @property
    def override_until(self) -> Optional[datetime]:
        return self._override_until

    @property
    def active_rule(self) -> Optional[str]:
        return self._active_rule

    def _is_overridden(self) -> bool:
        if self._override_until is None:
            return False
        return datetime.now(timezone.utc) < self._override_until

    def _rain_forecast_2h(self) -> bool:
        if not weather_client.forecast:
            return False
        now_ts = datetime.now(timezone.utc).timestamp()
        cutoff_ts = now_ts + RAIN_FORECAST_WINDOW_S
        return any(
            entry.get("pop", 0.0) >= RAIN_FORECAST_POP_THRESHOLD
            for entry in weather_client.forecast
            if entry.get("dt", 0) <= cutoff_ts
        )

    async def _evaluate(self) -> None:
        cfg = get_config()
        if not cfg.automation_enabled or self._is_overridden():
            if self._is_overridden():
                self._active_rule = "manual override"
                log_store.add_automation("manual_override", "Manual override active", False)
            else:
                self._active_rule = "automation disabled"
                log_store.add_automation("automation_disabled", "Automation disabled", False)
            return

        obs = weather_client.latest_obs
        staleness = weather_client.seconds_since_last_obs

        if not obs or staleness is None:
            self._active_rule = "waiting for weather data"
            log_store.add_automation("no_weather_data", "Waiting for weather data", False)
            return

        if staleness > WEATHER_TIMEOUT_S:
            self._active_rule = f"weather data timeout ({staleness:.0f}s) → retracting"
            self._sunny_conditions_met_since = None
            action_taken = None
            if awning_client.current_state != "undeployed":
                logger.warning("Weather data stale for %.0fs, undeploying awning", staleness)
                await awning_client.undeploy()
                action_taken = "undeploy"
                self._deployed_by_sunny = False
                self._deploy_started_at = None
            log_store.add_automation(
                "weather_timeout", self._active_rule, triggered=True, action_taken=action_taken
            )
            return

        rain_now = (obs.get("precip_type", 0) != 0) or (obs.get("rain_prev_min_mm", 0.0) > 0)
        wind_mph = obs.get("wind_avg_m_s", 0.0) * MPH_PER_MS
        lux = obs.get("illuminance_lux", 0)

        temp_c = obs.get("air_temp_c")
        unit = cfg.temp_unit
        unit_label = "°C" if unit == "C" else "°F"
        if temp_c is not None:
            warm = temp_c >= cfg.min_temp_c
            display_temp: Optional[float] = temp_c if unit == "C" else temp_c * 9.0 / 5.0 + 32.0
        else:
            warm = True
            display_temp = None
            if cfg.sunny_deploy_enabled and cfg.min_temp_c > 0:
                logger.warning("air_temp_c missing from obs; temperature check skipped")

        if cfg.rain_triggers_retract and rain_now:
            self._active_rule = "rain detected → retracting"
            self._sunny_conditions_met_since = None
            action_taken = None
            if awning_client.current_state != "undeployed":
                logger.info("Rain detected, undeploying awning")
                await awning_client.undeploy()
                action_taken = "undeploy"
                self._deployed_by_sunny = False
                self._deploy_started_at = None
            log_store.add_automation("rain_protection", self._active_rule, triggered=True, action_taken=action_taken)
            log_store.add_weather(
                obs, wind_mph,
                triggered_automation="rain_protection",
                triggered_action=action_taken,
                trigger_field="precip",
                trigger_reason=(
                    f"rain detected (type={obs.get('precip_type', 0)}, "
                    f"{obs.get('rain_prev_min_mm', 0.0):.1f} mm/min)"
                ),
            )
            return

        if cfg.wind_protection_enabled and wind_mph > cfg.max_wind_mph:
            self._active_rule = f"wind {wind_mph:.1f} mph > {cfg.max_wind_mph} mph → retracting"
            self._sunny_conditions_met_since = None
            action_taken = None
            if awning_client.current_state != "undeployed":
                logger.info("High wind (%.1f mph), undeploying awning", wind_mph)
                await awning_client.undeploy()
                action_taken = "undeploy"
                self._deployed_by_sunny = False
                self._deploy_started_at = None
            log_store.add_automation("wind_protection", self._active_rule, triggered=True, action_taken=action_taken)
            log_store.add_weather(
                obs, wind_mph,
                triggered_automation="wind_protection",
                triggered_action=action_taken,
                trigger_field="wind_avg_mph",
                trigger_reason=f"wind {wind_mph:.1f} mph > {cfg.max_wind_mph} mph max",
            )
            return

        sunny = lux > cfg.sunny_lux_threshold
        calm = wind_mph < cfg.sunny_wind_max_mph
        rain_forecast = self._rain_forecast_2h()

        sunny_conditions_met = (
            cfg.sunny_deploy_enabled
            and sunny
            and calm
            and warm
            and not rain_now
            and not rain_forecast
        )

        if sunny_conditions_met:
            if self._sunny_conditions_met_since is None:
                self._sunny_conditions_met_since = datetime.now(timezone.utc)

            dwell_elapsed = (datetime.now(timezone.utc) - self._sunny_conditions_met_since).total_seconds()
            remaining = cfg.sunny_deploy_dwell_s - dwell_elapsed

            temp_str = f", {display_temp:.0f}{unit_label}" if display_temp is not None else ""

            if remaining > 0:
                self._active_rule = (
                    f"sunny ({lux} lux) & calm ({wind_mph:.1f} mph){temp_str} "
                    f"→ waiting {remaining:.0f}s before deploy"
                )
                log_store.add_automation("sunny_deploy_waiting", self._active_rule, triggered=False)
                log_store.add_weather(obs, wind_mph)
                return

            action_taken = None
            if not self._deployed_by_sunny:
                logger.info(
                    "Sunny and calm (%.1f mph, %d lux%s), deploying for %ds",
                    wind_mph, lux, temp_str, cfg.deploy_duration_s,
                )
                await awning_client.deploy()
                self._deployed_by_sunny = True
                self._deploy_started_at = datetime.now(timezone.utc)
                action_taken = "deploy"
            elif self._deploy_started_at is not None:
                motor_elapsed = (datetime.now(timezone.utc) - self._deploy_started_at).total_seconds()
                if motor_elapsed >= cfg.deploy_duration_s:
                    await awning_client.stop()
                    self._deploy_started_at = None
                    action_taken = "stop"

            self._active_rule = f"sunny ({lux} lux) & calm ({wind_mph:.1f} mph){temp_str} → deployed"
            log_store.add_automation("sunny_deploy", self._active_rule, triggered=True, action_taken=action_taken)
            log_store.add_weather(
                obs, wind_mph,
                triggered_automation="sunny_deploy",
                triggered_action=action_taken,
                trigger_field="lux",
                trigger_reason=(
                    f"sunny ({lux} lux > {cfg.sunny_lux_threshold} threshold) "
                    f"& calm ({wind_mph:.1f} mph < {cfg.sunny_wind_max_mph} mph)"
                ),
            )
            return

        # Sunny conditions no longer met — reset dwell timer and retract if we deployed
        self._sunny_conditions_met_since = None
        if self._deployed_by_sunny and awning_client.current_state == "deployed":
            self._active_rule = "sunny conditions no longer met → retracting"
            logger.info("Sunny conditions ended, retracting awning")
            await awning_client.undeploy()
            self._deployed_by_sunny = False
            self._deploy_started_at = None
            log_store.add_automation(
                "sunny_retract", self._active_rule, triggered=True, action_taken="undeploy"
            )
            log_store.add_weather(
                obs, wind_mph,
                triggered_automation="sunny_retract",
                triggered_action="undeploy",
                trigger_field="lux",
                trigger_reason="sunny deploy conditions no longer met",
            )
            return

        if cfg.sunny_deploy_enabled:
            if rain_now:
                block = "rain detected"
            elif rain_forecast:
                block = "rain forecast within 2h"
            elif not sunny:
                block = f"lux {lux} < {cfg.sunny_lux_threshold} threshold"
            elif not calm:
                block = f"wind {wind_mph:.1f} mph > {cfg.sunny_wind_max_mph} mph"
            elif not warm:
                t = f"{display_temp:.0f}{unit_label}" if display_temp is not None else "unknown"
                threshold = cfg.min_temp_c if unit == "C" else cfg.min_temp_c * 9.0 / 5.0 + 32.0
                block = f"temp {t} < {threshold:.0f}{unit_label} threshold"
            else:
                block = "conditions not met"
            self._active_rule = f"sunny deploy waiting — {block}"
        else:
            self._active_rule = "conditions nominal — no action"
        log_store.add_automation("conditions_nominal", self._active_rule, triggered=False)
        log_store.add_weather(obs, wind_mph)

    async def run(self) -> None:
        while True:
            try:
                await self._evaluate()
            except Exception as exc:
                logger.error("Automation engine error: %s", exc)
            await asyncio.sleep(EVAL_INTERVAL_S)


automation_engine = AutomationEngine()
