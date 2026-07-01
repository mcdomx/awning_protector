import asyncio
import logging
import os
import time
from typing import Literal, Optional

import httpx

logger = logging.getLogger(__name__)

AWNING_URL = os.getenv("AWNING_URL", "http://localhost:8765")

AwningState = Literal["deployed", "undeployed", "unknown"]


class AwningClient:
    def __init__(self) -> None:
        self.current_state: AwningState = "unknown"
        self._deploy_start_at: Optional[float] = None
        self._deploy_duration_s: Optional[int] = None

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
        return ok

    async def undeploy(self) -> bool:
        ok = await self._call("/awning/undeploy")
        if ok:
            self.current_state = "undeployed"
            self._deploy_start_at = None
            self._deploy_duration_s = None
        return ok

    async def stop(self) -> bool:
        return await self._call("/awning/stop")

    async def deploy_timed(self, duration_s: int) -> None:
        ok = await self.deploy()
        if ok:
            await asyncio.sleep(duration_s)
            await self.stop()

    def record_timed_deploy(self, duration_s: int) -> None:
        """Record the start of a fresh timed deployment."""
        self._deploy_start_at = time.time()
        self._deploy_duration_s = duration_s
        self.current_state = "deployed"

    def extend_timed_deploy(self, new_total_s: int) -> None:
        """Update the planned total duration without resetting the start time."""
        self._deploy_duration_s = new_total_s
        self.current_state = "deployed"

    def remaining_deploy_s(self) -> Optional[int]:
        """Seconds remaining in the current timed deployment, or None if not tracking."""
        if self._deploy_start_at is None or self._deploy_duration_s is None:
            return None
        elapsed = time.time() - self._deploy_start_at
        return max(0, int(self._deploy_duration_s - elapsed))

    def effective_timed_deploy_s(self, requested_s: int) -> Optional[int]:
        """
        Seconds to pass to the timed-deploy endpoint given a total requested duration.
        Accounts for already-elapsed deployment time so the awning is deployed for
        exactly `requested_s` seconds from the original deploy start, not from now.
        Returns None when the current deployment already covers the requested duration.
        """
        if self.current_state != "deployed" or self._deploy_start_at is None:
            return requested_s
        elapsed = int(time.time() - self._deploy_start_at)
        effective = requested_s - elapsed
        return effective if effective > 0 else None


awning_client = AwningClient()
