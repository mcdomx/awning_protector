import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .awning import awning_client
from .config import get_config
from .weather import weather_client

logger = logging.getLogger(__name__)

MPH_PER_MS = 2.23694
EVAL_INTERVAL_S = 10


class AutomationEngine:
    def __init__(self) -> None:
        self._override_until: Optional[datetime] = None
        self._last_action: Optional[str] = None
        self._active_rule: Optional[str] = None

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
            else:
                self._active_rule = "automation disabled"
            return

        obs = weather_client.latest_obs
        if not obs:
            self._active_rule = "waiting for weather data"
            return

        rain_now = (obs.get("precip_type", 0) != 0) or (obs.get("rain_prev_min_mm", 0.0) > 0)
        wind_mph = obs.get("wind_avg_m_s", 0.0) * MPH_PER_MS
        lux = obs.get("illuminance_lux", 0)

        if cfg.rain_triggers_retract and rain_now:
            self._active_rule = "rain detected → retracting"
            if awning_client.current_state != "undeployed":
                logger.info("Rain detected, undeploying awning")
                await awning_client.undeploy()
            return

        if cfg.wind_protection_enabled and wind_mph > cfg.max_wind_mph:
            self._active_rule = f"wind {wind_mph:.1f} mph > {cfg.max_wind_mph} mph → retracting"
            if awning_client.current_state != "undeployed":
                logger.info("High wind (%.1f mph), undeploying awning", wind_mph)
                await awning_client.undeploy()
            return

        sunny = lux > cfg.sunny_lux_threshold
        calm = wind_mph < cfg.sunny_wind_max_mph
        if cfg.sunny_deploy_enabled and sunny and calm and not rain_now:
            self._active_rule = (
                f"sunny ({lux} lux) & calm ({wind_mph:.1f} mph) → deploying {cfg.deploy_duration_s}s"
            )
            if awning_client.current_state != "deployed":
                logger.info(
                    "Sunny and calm (%.1f mph, %d lux), deploying for %ds",
                    wind_mph, lux, cfg.deploy_duration_s,
                )
                await awning_client.deploy_timed(cfg.deploy_duration_s)
            return

        self._active_rule = "conditions nominal — no action"

    async def run(self) -> None:
        while True:
            try:
                await self._evaluate()
            except Exception as exc:
                logger.error("Automation engine error: %s", exc)
            await asyncio.sleep(EVAL_INTERVAL_S)


automation_engine = AutomationEngine()
