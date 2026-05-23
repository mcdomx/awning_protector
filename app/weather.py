import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import httpx

logger = logging.getLogger(__name__)

WEATHER_URL = os.getenv("WEATHER_URL", "http://localhost:8766")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
LATITUDE = os.getenv("LATITUDE", "")
LONGITUDE = os.getenv("LONGITUDE", "")
FORECAST_INTERVAL_S = 900  # 15 minutes


class WeatherClient:
    def __init__(self) -> None:
        self.latest_obs: Dict[str, Any] = {}
        self.latest_wind: Dict[str, Any] = {}
        self.forecast: List[Dict[str, Any]] = []
        self.forecast_error: Optional[str] = None
        self._subscribers: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self._last_forecast_fetch: Optional[datetime] = None
        self._last_obs_at: Optional[datetime] = None

    @property
    def seconds_since_last_obs(self) -> Optional[float]:
        if self._last_obs_at is None:
            return None
        return (datetime.now(timezone.utc) - self._last_obs_at).total_seconds()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def _fan_out(self, message: Dict[str, Any]) -> None:
        dead: Set[asyncio.Queue] = set()
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                dead.add(q)
        self._subscribers -= dead

    async def _sse_loop(self) -> None:
        url = f"{WEATHER_URL}/weather/stream"
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
                                msg = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            msg_type = msg.get("type")
                            data = msg.get("data", {})
                            if msg_type == "obs_st":
                                self.latest_obs = data
                                self._last_obs_at = datetime.now(timezone.utc)
                            elif msg_type == "rapid_wind":
                                self.latest_wind = data
                            await self._fan_out(msg)
            except Exception as exc:
                logger.warning("SSE stream error, reconnecting in 5s: %s", exc)
                await asyncio.sleep(5)

    async def _forecast_loop(self) -> None:
        while True:
            await self._fetch_forecast()
            await asyncio.sleep(FORECAST_INTERVAL_S)

    async def _fetch_forecast(self) -> None:
        if not (OPENWEATHER_API_KEY and LATITUDE and LONGITUDE):
            return
        url = (
            "https://api.openweathermap.org/data/2.5/forecast"
            f"?lat={LATITUDE}&lon={LONGITUDE}&appid={OPENWEATHER_API_KEY}&cnt=8&units=metric"
        )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            self.forecast = [
                {
                    "dt": entry["dt"],
                    "pop": entry.get("pop", 0.0),
                    "description": entry["weather"][0]["description"] if entry.get("weather") else "",
                    "wind_mph": round(entry.get("wind", {}).get("speed", 0.0) * 2.23694, 1),
                    "temp_c": round(entry.get("main", {}).get("temp", 0.0), 1),
                }
                for entry in data.get("list", [])
            ]
            self.forecast_error = None
            self._last_forecast_fetch = datetime.now(timezone.utc)
        except Exception as exc:
            self.forecast_error = str(exc)
            logger.warning("Forecast fetch failed: %s", exc)

    def current_snapshot(self) -> Dict[str, Any]:
        return {
            "obs": self.latest_obs,
            "wind": self.latest_wind,
            "forecast": self.forecast,
            "forecast_error": self.forecast_error,
            "forecast_updated_at": self._last_forecast_fetch.isoformat() if self._last_forecast_fetch else None,
        }

    async def start(self) -> None:
        asyncio.create_task(self._sse_loop())
        asyncio.create_task(self._forecast_loop())


weather_client = WeatherClient()
