import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

UV_SENSOR_URL = os.getenv("UV_SENSOR_URL", "http://uvsensor.local:8768")


class UVSensorClient:
    def __init__(self) -> None:
        self.latest_reading: Dict[str, Any] = {}
        self._last_reading_at: Optional[datetime] = None
        self._new_reading_event: asyncio.Event = asyncio.Event()

    @property
    def seconds_since_last_reading(self) -> Optional[float]:
        if self._last_reading_at is None:
            return None
        return (datetime.now(timezone.utc) - self._last_reading_at).total_seconds()

    async def wait_for_reading(self) -> None:
        await self._new_reading_event.wait()
        self._new_reading_event.clear()

    async def _sse_loop(self) -> None:
        url = f"{UV_SENSOR_URL}/uv/stream"
        while True:
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", url) as resp:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            raw = line[len("data:"):].strip()
                            if not raw:
                                continue
                            try:
                                reading = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            self.latest_reading = reading
                            self._last_reading_at = datetime.now(timezone.utc)
                            self._new_reading_event.set()
            except Exception as exc:
                logger.warning("UV sensor SSE stream error, reconnecting in 5s: %s", exc)
                await asyncio.sleep(5)

    async def start(self) -> None:
        asyncio.create_task(self._sse_loop())


uv_sensor_client = UVSensorClient()
