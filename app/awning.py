import asyncio
import logging
import os
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

AWNING_URL = os.getenv("AWNING_URL", "http://localhost:8765")

AwningState = Literal["deployed", "undeployed", "unknown"]


class AwningClient:
    def __init__(self) -> None:
        self.current_state: AwningState = "unknown"

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
        return ok

    async def stop(self) -> bool:
        return await self._call("/awning/stop")

    async def deploy_timed(self, duration_s: int) -> None:
        ok = await self.deploy()
        if ok:
            await asyncio.sleep(duration_s)
            await self.stop()


awning_client = AwningClient()
