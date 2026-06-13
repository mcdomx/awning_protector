import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import jinja2
from anthropic import Anthropic
from anthropic.types import Message

from .config import get_config
from .weather import weather_client

_LOCAL_TZ = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)

# Fields the worker prompts read directly off `weather_client.latest_obs`.
# An evaluation run on an observation missing any of these would feed the
# workers placeholder values (e.g. "Temperature: None°C"), so we wait for a
# complete reading before spending tokens on an evaluation.
REQUIRED_OBS_FIELDS = (
    "air_temp_c",
    "wind_avg_m_s",
    "precip_type",
    "rain_prev_min_mm",
    "illuminance_lux",
    "uv_index",
)

INCOMPLETE_OBS_RETRY_S = 30


def _missing_obs_fields() -> list:
    obs = weather_client.latest_obs
    if not obs:
        return list(REQUIRED_OBS_FIELDS)
    return [name for name in REQUIRED_OBS_FIELDS if obs.get(name) is None]

def _parse_deploy_hour(time_str: str) -> int:
    """Parse '8AM' or '6PM' to a 24-hour integer (0–23)."""
    return datetime.strptime(time_str.strip().upper(), "%I%p").hour


def _within_deploy_window(
    earliest_str: str, latest_str: str, now: Optional[datetime] = None
) -> bool:
    if now is None:
        now = datetime.now(_LOCAL_TZ)
    h = now.hour
    return _parse_deploy_hour(earliest_str) <= h < _parse_deploy_hour(latest_str)


def _next_window_open_at(earliest_str: str, now: Optional[datetime] = None) -> datetime:
    """Return the next UTC datetime when the deploy window opens."""
    if now is None:
        now = datetime.now(_LOCAL_TZ)
    earliest_h = _parse_deploy_hour(earliest_str)
    candidate = now.replace(hour=earliest_h, minute=0, second=0, microsecond=0)
    if candidate > now:
        return candidate.astimezone(timezone.utc)
    return (candidate + timedelta(days=1)).astimezone(timezone.utc)


PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

VALID_PROMPT_NAMES = {"wind", "rain", "forecast", "solar", "coordinator", "orchestrator"}
_PROMPT_FILE = {
    "wind": "wind_worker.md.j2",
    "rain": "rain_worker.md.j2",
    "forecast": "forecast_worker.md.j2",
    "solar": "solar_worker.md.j2",
    "coordinator": "coordinator.md.j2",
    "orchestrator": "orchestrator.md.j2",
}

_jinja_env = jinja2.Environment(
    loader=jinja2.BaseLoader(),
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / _PROMPT_FILE[name]).read_text()


def save_prompt(name: str, content: str) -> None:
    (PROMPTS_DIR / _PROMPT_FILE[name]).write_text(content)


def _require_env(name: str) -> str:
    """Return a populated .env value, or raise a clear, actionable error.

    The anthropic SDK treats an empty-string env var as "present" and only fails
    deep inside the first API call. Validate up front so a missing/blank value
    produces an obvious message that surfaces in the evaluation report.
    """
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set. Add a value for it in your .env file "
            "to enable AI evaluations."
        )
    return value


def _build_system_blocks(name: str, cfg, **extra_vars) -> list:
    text = _jinja_env.from_string(load_prompt(name)).render(**cfg.model_dump(), **extra_vars)
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


class _Claude:
    def __init__(self, model: str) -> None:
        self.client = Anthropic(api_key=_require_env("ANTHROPIC_API_KEY"))
        self.model = model

    @staticmethod
    def add_user_message(messages: list, message) -> None:
        messages.append({
            "role": "user",
            "content": message.content if isinstance(message, Message) else message,
        })

    @staticmethod
    def add_assistant_message(messages: list, message) -> None:
        messages.append({
            "role": "assistant",
            "content": message.content if isinstance(message, Message) else message,
        })

    @staticmethod
    def text_from_message(message: Message) -> str:
        return "\n".join(
            block.text for block in message.content if block.type == "text"
        )

    def chat(
        self,
        messages: list,
        system_blocks: list = None,
        temperature: float = 0,
        tools=None,
        streaming: bool = False,
    ) -> Message:
        params = {
            "model": self.model,
            "max_tokens": 8000,
            "messages": messages,
            "temperature": temperature,
        }
        if system_blocks:
            params["system"] = system_blocks
        if tools:
            params["tools"] = tools
        if streaming:
            with self.client.messages.stream(**params) as stream:
                return stream.get_final_message()
        return self.client.messages.create(**params)


class AIEngine:
    def __init__(self) -> None:
        self._last_eval_text: Optional[str] = None
        self._last_eval_at: Optional[datetime] = None
        self._next_eval_at: Optional[datetime] = None
        self._is_running: bool = False
        self._wakeup: Optional[asyncio.Event] = None

    @property
    def last_eval_text(self) -> Optional[str]:
        return self._last_eval_text

    @property
    def last_eval_at(self) -> Optional[datetime]:
        return self._last_eval_at

    @property
    def next_eval_at(self) -> Optional[datetime]:
        return self._next_eval_at

    @property
    def is_running(self) -> bool:
        return self._is_running

    def trigger_immediate(self) -> None:
        if not get_config().ai.ai_enabled:
            return
        self._next_eval_at = datetime.now(timezone.utc)
        if self._wakeup is not None:
            self._wakeup.set()

    def notify_config_changed(self) -> None:
        """Wake the run loop so it re-checks ai_enabled without waiting for the next sleep to expire."""
        if self._wakeup is not None:
            self._wakeup.set()

    async def run(self) -> None:
        self._wakeup = asyncio.Event()

        while True:
            self._wakeup.clear()

            cfg = get_config()
            if not cfg.ai.ai_enabled:
                self._next_eval_at = None
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
                continue

            now = datetime.now(timezone.utc)
            if self._next_eval_at and now < self._next_eval_at:
                sleep_secs = (self._next_eval_at - now).total_seconds()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=sleep_secs)
                except asyncio.TimeoutError:
                    pass
                continue

            ai_cfg = cfg.ai
            now_local = datetime.now(_LOCAL_TZ)
            if not _within_deploy_window(
                ai_cfg.earliest_auto_deployment, ai_cfg.latest_auto_deployment, now=now_local
            ):
                next_open = _next_window_open_at(ai_cfg.earliest_auto_deployment, now=now_local)
                self._last_eval_text = (
                    f"Outside deployment window "
                    f"({ai_cfg.earliest_auto_deployment}–{ai_cfg.latest_auto_deployment}). "
                    f"Next evaluation at {ai_cfg.earliest_auto_deployment}."
                )
                self._last_eval_at = datetime.now(timezone.utc)
                self._next_eval_at = next_open
                logger.info(
                    "Outside deploy window (%s–%s); next eval at %s",
                    ai_cfg.earliest_auto_deployment,
                    ai_cfg.latest_auto_deployment,
                    next_open.isoformat(),
                )
                continue

            missing = _missing_obs_fields()
            if missing:
                logger.info(
                    "Skipping AI evaluation — incomplete weather reading (missing: %s)",
                    ", ".join(missing),
                )
                self._last_eval_text = (
                    f"Evaluation skipped — incomplete weather reading (missing: {', '.join(missing)})"
                )
                self._last_eval_at = datetime.now(timezone.utc)
                self._next_eval_at = self._last_eval_at + timedelta(seconds=INCOMPLETE_OBS_RETRY_S)
                continue

            self._is_running = True
            try:
                from .ai_pipeline import run_ai_pipeline  # lazy import — avoids ai_agent <-> ai_pipeline cycle
                from .git_sync import git_sync  # lazy import — avoids ai_agent <-> git_sync cycle

                await git_sync.pull_if_changed()
                result = await asyncio.to_thread(run_ai_pipeline, cfg.ai)
                if get_config().ai.ai_enabled:
                    self._last_eval_text = result["evaluation_text"]
                    self._last_eval_at = datetime.now(timezone.utc)
                    next_secs = result["next_eval_seconds"]
                    self._next_eval_at = self._last_eval_at + timedelta(seconds=next_secs)
                    logger.info(
                        "AI agent evaluation complete. Next evaluation in %ds", next_secs
                    )
                else:
                    logger.info("AI disabled while evaluation was running; discarding result")
                    self._next_eval_at = None
            except Exception as exc:
                logger.error("AI agent error: %s", exc)
                if get_config().ai.ai_enabled:
                    self._last_eval_text = f"Evaluation error: {exc}"
                    self._last_eval_at = datetime.now(timezone.utc)
                    self._next_eval_at = self._last_eval_at + timedelta(seconds=300)
            finally:
                self._is_running = False


ai_engine = AIEngine()
