import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .ai_agent import ai_engine
from .awning import awning_client
from .config import get_config
from .log_store import log_store
from .weather import weather_client

logger = logging.getLogger(__name__)

MPH_PER_MS = 2.23694
EVAL_INTERVAL_S = 10
WEATHER_TIMEOUT_S = 300  # retract after 5 minutes with no obs_st


class AutomationEngine:
    def __init__(self) -> None:
        self._override_until: Optional[datetime] = None
        self._last_action: Optional[str] = None
        self._active_rule: Optional[str] = None
        self._weather_timed_out: bool = False

    def set_manual_override(self) -> None:
        cfg = get_config()
        self._override_until = datetime.now(timezone.utc) + timedelta(minutes=cfg.manual_override_min)
        self._active_rule = "manual override"

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
            self._weather_timed_out = True
            action_taken = None
            if awning_client.current_state != "undeployed":
                logger.warning("Weather data stale for %.0fs, undeploying awning", staleness)
                await awning_client.undeploy()
                action_taken = "undeploy"
            log_store.add_automation(
                "weather_timeout", self._active_rule, triggered=True, action_taken=action_taken
            )
            return

        if self._weather_timed_out:
            self._weather_timed_out = False
            if cfg.ai.ai_enabled:
                logger.info("Weather data recovered after timeout — triggering AI evaluation")
                ai_engine.trigger_immediate()

        rain_now = (obs.get("precip_type", 0) != 0) or (obs.get("rain_prev_min_mm", 0.0) > 0)
        wind_mph = obs.get("wind_avg_m_s", 0.0) * MPH_PER_MS

        if cfg.rain_triggers_retract and rain_now:
            self._active_rule = "rain detected → retracting"
            action_taken = None
            if awning_client.current_state != "undeployed":
                logger.info("Rain detected, undeploying awning")
                await awning_client.undeploy()
                action_taken = "undeploy"
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
            action_taken = None
            if awning_client.current_state != "undeployed":
                logger.info("High wind (%.1f mph), undeploying awning", wind_mph)
                await awning_client.undeploy()
                action_taken = "undeploy"
            log_store.add_automation("wind_protection", self._active_rule, triggered=True, action_taken=action_taken)
            log_store.add_weather(
                obs, wind_mph,
                triggered_automation="wind_protection",
                triggered_action=action_taken,
                trigger_field="wind_avg_mph",
                trigger_reason=f"wind {wind_mph:.1f} mph > {cfg.max_wind_mph} mph max",
            )
            return

        if cfg.ai.ai_enabled:
            self._active_rule = "AI mode active — deployment decisions delegated to AI agent"
            log_store.add_weather(obs, wind_mph)
            return

        self._active_rule = "conditions nominal — no action"
        log_store.add_automation("conditions_nominal", self._active_rule, triggered=False)
        log_store.add_weather(obs, wind_mph)

    async def _wind_guard(self) -> None:
        while True:
            try:
                await weather_client.wait_for_wind_data()
                cfg = get_config()
                if not cfg.automation_enabled or not cfg.wind_protection_enabled or self._is_overridden():
                    continue
                staleness = weather_client.seconds_since_last_obs
                if staleness is not None and staleness > WEATHER_TIMEOUT_S:
                    continue
                obs = weather_client.latest_obs
                rapid = weather_client.latest_wind
                wind_avg_mph = obs.get("wind_avg_m_s", 0.0) * MPH_PER_MS if obs else 0.0
                wind_rapid_mph = rapid.get("wind_speed_m_s", 0.0) * MPH_PER_MS if rapid else 0.0
                wind_mph = max(wind_avg_mph, wind_rapid_mph)
                if wind_mph > cfg.max_wind_mph and awning_client.current_state != "undeployed":
                    logger.info(
                        "Wind guard: %.1f mph > %.1f mph — retracting immediately",
                        wind_mph, cfg.max_wind_mph,
                    )
                    rule_description = (
                        f"wind guard: {wind_mph:.1f} mph > {cfg.max_wind_mph} mph → retracting immediately"
                    )
                    await awning_client.undeploy()
                    log_store.add_automation(
                        "wind_guard", rule_description, triggered=True, action_taken="undeploy"
                    )
                    if obs:
                        log_store.add_weather(
                            obs, wind_avg_mph,
                            triggered_automation="wind_guard",
                            triggered_action="undeploy",
                            trigger_field="wind_avg_mph",
                            trigger_reason=rule_description,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Wind guard error: %s", exc)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._evaluate()
            except Exception as exc:
                logger.error("Automation engine error: %s", exc)
            await asyncio.sleep(EVAL_INTERVAL_S)

    async def run(self) -> None:
        await asyncio.gather(self._poll_loop(), self._wind_guard())


automation_engine = AutomationEngine()
