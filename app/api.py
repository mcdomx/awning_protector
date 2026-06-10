import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .ai_agent import VALID_PROMPT_NAMES, ai_engine, load_prompt, save_prompt
from .automation import automation_engine
from .awning import awning_client
from .config import AutomationConfig, get_config, save_config
from .git_sync import git_sync
from .log_store import AutomationLogEntry, WeatherLogEntry, log_store
from .weather import weather_client

logger = logging.getLogger(__name__)

WEATHER_URL = os.getenv("WEATHER_URL", "http://localhost:8766")

STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await weather_client.start()
    asyncio.create_task(automation_engine.run())
    asyncio.create_task(ai_engine.run())
    asyncio.create_task(git_sync.fetch_remote_shas())
    yield


app = FastAPI(title="Awning Protector", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text())


@app.get("/weather/current")
async def weather_current() -> Dict[str, Any]:
    return weather_client.current_snapshot()


@app.get("/weather/forecast/hourly")
async def weather_forecast_hourly() -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{WEATHER_URL}/weather/forecast/hourly")
            resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Forecast unavailable: {exc}")


@app.get("/weather/forecast/daily")
async def weather_forecast_daily() -> Dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{WEATHER_URL}/weather/forecast/daily")
            resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Forecast unavailable: {exc}")


async def _sse_event_stream() -> AsyncIterator[str]:
    q = weather_client.subscribe()
    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=30)
                yield f"data: {json.dumps(msg)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        weather_client.unsubscribe(q)


@app.get("/weather/stream")
async def weather_stream() -> StreamingResponse:
    return StreamingResponse(
        _sse_event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/awning/status")
async def awning_status() -> Dict[str, Any]:
    override = automation_engine.override_until
    return {
        "state": awning_client.current_state,
        "automation_active": get_config().automation_enabled and (
            override is None or override.replace(tzinfo=timezone.utc) < override
        ),
        "override_until": override.isoformat() if override else None,
        "active_rule": automation_engine.active_rule,
    }


@app.post("/awning/deploy")
async def awning_deploy() -> Dict[str, str]:
    automation_engine.set_manual_override()
    ok = await awning_client.deploy()
    if not ok:
        raise HTTPException(status_code=502, detail="Awning service error")
    return {"status": "ok"}


@app.post("/awning/undeploy")
async def awning_undeploy() -> Dict[str, str]:
    automation_engine.set_manual_override()
    ok = await awning_client.undeploy()
    if not ok:
        raise HTTPException(status_code=502, detail="Awning service error")
    return {"status": "ok"}


@app.post("/awning/stop")
async def awning_stop() -> Dict[str, str]:
    automation_engine.set_manual_override()
    ok = await awning_client.stop()
    if not ok:
        raise HTTPException(status_code=502, detail="Awning service error")
    return {"status": "ok"}


@app.get("/config")
async def config_get() -> AutomationConfig:
    return get_config()


@app.put("/config")
async def config_put(cfg: AutomationConfig) -> AutomationConfig:
    save_config(cfg)
    ai_engine.notify_config_changed()
    return cfg


@app.get("/ai/status")
async def ai_status() -> Dict[str, Any]:
    last_at = ai_engine.last_eval_at
    next_at = ai_engine.next_eval_at
    return {
        "enabled": get_config().ai.ai_enabled,
        "is_running": ai_engine.is_running,
        "last_eval_text": ai_engine.last_eval_text,
        "last_eval_at": last_at.isoformat() if last_at else None,
        "next_eval_at": next_at.isoformat() if next_at else None,
    }


@app.get("/ai/prompts/{name}")
async def ai_prompt_get(name: str) -> Dict[str, str]:
    if name not in VALID_PROMPT_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {name}")
    return {"name": name, "content": load_prompt(name)}


@app.put("/ai/prompts/{name}")
async def ai_prompt_put(name: str, body: Dict[str, str]) -> Dict[str, str]:
    if name not in VALID_PROMPT_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown prompt: {name}")
    content = body.get("content", "")
    save_prompt(name, content)
    return {"name": name, "content": content}


@app.post("/ai/evaluate")
async def ai_evaluate() -> Dict[str, str]:
    ai_engine.trigger_immediate()
    return {"status": "evaluation scheduled"}


@app.get("/ai/git-status")
async def ai_git_status() -> Dict[str, Any]:
    return git_sync.status()


@app.post("/ai/git-push")
async def ai_git_push() -> Dict[str, Any]:
    try:
        results = await git_sync.push_prompts()
        return {"status": "ok", "results": results}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/logs/automation-page", response_class=HTMLResponse)
async def automation_log_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "automation_log.html").read_text())


@app.get("/logs/weather-page", response_class=HTMLResponse)
async def weather_log_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "weather_log.html").read_text())


@app.get("/charts", response_class=HTMLResponse)
async def charts_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "charts.html").read_text())


@app.get("/logs/automation", response_model=List[AutomationLogEntry])
async def logs_automation() -> List[AutomationLogEntry]:
    return log_store.get_automation()


@app.get("/logs/weather", response_model=List[WeatherLogEntry])
async def logs_weather() -> List[WeatherLogEntry]:
    return log_store.get_weather()
