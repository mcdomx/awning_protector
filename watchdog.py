"""
Watchdog for the awning_protector app.

Polls the app's /health endpoint. If the app is unreachable for FAILURE_TIMEOUT_S
(default 120s), it retracts the awning by calling the tahoma_awning service directly.
Resumes monitoring silently once the app recovers.

Run alongside the main app:
    pipenv run python watchdog.py
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watchdog] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

APP_URL = os.getenv("APP_URL", f"http://localhost:{os.getenv('APP_PORT', '8767')}")
AWNING_URL = os.getenv("AWNING_URL", "http://localhost:8765")
POLL_INTERVAL_S = 30
FAILURE_TIMEOUT_S = 120


async def _retract_awning(client: httpx.AsyncClient) -> None:
    url = f"{AWNING_URL}/awning/undeploy"
    try:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        logger.info("Awning retracted via tahoma_awning service")
    except Exception as exc:
        logger.error("Failed to retract awning: %s", exc)


async def run() -> None:
    unhealthy_since: Optional[datetime] = None
    retract_triggered: bool = False

    logger.info("Watchdog started — polling %s every %ds", APP_URL, POLL_INTERVAL_S)

    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(POLL_INTERVAL_S)
            try:
                resp = await client.get(f"{APP_URL}/health", timeout=10)
                resp.raise_for_status()
                if unhealthy_since is not None:
                    down_s = (datetime.now(timezone.utc) - unhealthy_since).total_seconds()
                    logger.info("App recovered after %.0fs — automation resumes", down_s)
                    unhealthy_since = None
                    retract_triggered = False
            except Exception as exc:
                now = datetime.now(timezone.utc)
                if unhealthy_since is None:
                    unhealthy_since = now
                    logger.warning("App health check failed: %s", exc)

                down_s = (now - unhealthy_since).total_seconds()

                if not retract_triggered and down_s >= FAILURE_TIMEOUT_S:
                    logger.warning(
                        "App unreachable for %.0fs (>= %ds) — retracting awning",
                        down_s, FAILURE_TIMEOUT_S,
                    )
                    await _retract_awning(client)
                    retract_triggered = True
                else:
                    logger.info("App still down (%.0fs elapsed)", down_s)


if __name__ == "__main__":
    asyncio.run(run())
