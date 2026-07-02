import asyncio
import logging
import os
from typing import Literal, Optional

import httpx

logger = logging.getLogger(__name__)

AWNING_URL = os.getenv("AWNING_URL", "http://localhost:8765")

AwningState = Literal["deployed", "undeployed", "unknown"]


class AwningClient:
    """
    The awning motor has no position feedback: /awning/deploy/timed?seconds=N runs
    the motor for N seconds of travel then stops, leaving the awning extended by
    that amount indefinitely (it never auto-retracts and extension never decays
    with wall-clock time). `_deployed_seconds` tracks that cumulative extension so
    repeated deploy calls for the same target don't keep re-running the motor.
    """

    def __init__(self) -> None:
        self.current_state: AwningState = "unknown"
        self._deployed_seconds: float = 0.0

    async def _call(self, path: str) -> bool:
        url = f"{AWNING_URL}{path}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            return True
        except Exception as exc:
            logger.error("Awning command %s failed: %s", path, exc)
            return False

    async def deploy(self) -> bool:
        ok = await self._call("/awning/deploy")
        if ok:
            self.current_state = "deployed"
            self._deployed_seconds = float("inf")  # ran to full physical extension
        return ok

    async def undeploy(self) -> bool:
        ok = await self._call("/awning/undeploy")
        if ok:
            self.current_state = "undeployed"
            self._deployed_seconds = 0.0
        return ok

    async def stop(self) -> bool:
        return await self._call("/awning/stop")

    async def deploy_timed(self, duration_s: int) -> None:
        ok = await self.deploy()
        if ok:
            await asyncio.sleep(duration_s)
            await self.stop()

    def deploy_delta_seconds(self, target_seconds: float) -> Optional[float]:
        """
        Seconds of additional motor travel needed to reach `target_seconds` of
        extension, or None if the awning is already extended that far. Extension
        does not decay over time, so this only depends on the last recorded
        position, never on how long ago it was set.
        """
        if self._deployed_seconds >= target_seconds:
            return None
        return target_seconds - self._deployed_seconds

    def record_deploy_extension(self, target_seconds: float) -> None:
        """Record that the awning is now extended to `target_seconds`."""
        self._deployed_seconds = target_seconds
        self.current_state = "deployed"

    def record_partial_retract(self, seconds: float) -> None:
        """Record that the awning retracted by `seconds` of motor travel."""
        self._deployed_seconds = max(0.0, self._deployed_seconds - seconds)
        self.current_state = "deployed" if self._deployed_seconds > 0 else "undeployed"

    def record_full_retract(self) -> None:
        self._deployed_seconds = 0.0
        self.current_state = "undeployed"

    def deployed_seconds(self) -> float:
        """Current extension amount, in seconds of motor travel."""
        return self._deployed_seconds


awning_client = AwningClient()
