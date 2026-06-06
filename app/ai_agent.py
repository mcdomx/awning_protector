import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import jinja2
from anthropic import Anthropic
from anthropic.types import Message

from .ai_tools import action_tool_schemas, build_weather_context, execute_action_tool
from .config import DATA_DIR, get_config
from .log_store import log_store

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
DATA_PROMPTS_DIR = DATA_DIR / "prompts"

VALID_PROMPT_NAMES = {"awning", "timing"}
_PROMPT_FILE = {"awning": "awning_agent.md.j2", "timing": "eval_timing_agent.md.j2"}

_jinja_env = jinja2.Environment(
    loader=jinja2.BaseLoader(),
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def load_prompt(name: str) -> str:
    user_path = DATA_PROMPTS_DIR / _PROMPT_FILE[name]
    if user_path.exists():
        return user_path.read_text()
    return (PROMPTS_DIR / _PROMPT_FILE[name]).read_text()


def save_prompt(name: str, content: str) -> None:
    DATA_PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_PROMPTS_DIR / _PROMPT_FILE[name]).write_text(content)


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


def _settings_table(cfg) -> str:
    lines = ["| Setting | Value |", "|---------|-------|"]
    lines += [f"| {k} | {v} |" for k, v in cfg.model_dump().items()]
    return "\n".join(lines)


def _build_awning_system_blocks(cfg) -> list:
    text = _jinja_env.from_string(load_prompt("awning")).render(
        settings_table=_settings_table(cfg),
        **cfg.model_dump(),
    )
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _build_eval_timing_system_blocks(cfg) -> list:
    text = _jinja_env.from_string(load_prompt("timing")).render(
        earliest_auto_deployment=cfg.earliest_auto_deployment,
        latest_auto_deployment=cfg.latest_auto_deployment,
        min_eval_interval_seconds=cfg.min_eval_interval_seconds,
    )
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


def _run_awning_agent(cfg) -> dict:
    model = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
    system_blocks = _build_awning_system_blocks(cfg)
    chat = _Claude(model=model)
    messages = []
    weather_context = build_weather_context()
    chat.add_user_message(
        messages,
        f"{weather_context}\n\nDetermine if the patio awning should be extended or retracted.",
    )

    while True:
        response = chat.chat(
            messages,
            system_blocks=system_blocks,
            temperature=0,
            tools=action_tool_schemas,
            streaming=True,
        )
        chat.add_assistant_message(messages, response)

        if response.stop_reason == "end_turn":
            evaluation_text = chat.text_from_message(response)
            try:
                next_eval_seconds = int(_get_next_eval_seconds(evaluation_text, cfg))
            except Exception as exc:
                logger.warning("Timing agent failed, using default interval: %s", exc)
                next_eval_seconds = cfg.min_eval_interval_seconds
            return {
                "evaluation_text": evaluation_text,
                "next_eval_seconds": next_eval_seconds,
            }

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = execute_action_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})


def _get_next_eval_seconds(assessment: str, cfg) -> str:
    model = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
    system_blocks = _build_eval_timing_system_blocks(cfg)
    chat = _Claude(model=model)
    messages = []
    chat.add_user_message(messages, assessment)
    response = chat.chat(messages, system_blocks=system_blocks, temperature=0)
    raw = next((b.text for b in response.content if b.type == "text"), "300")
    try:
        seconds = int(raw.strip())
    except ValueError:
        seconds = 300
    return str(max(seconds, cfg.min_eval_interval_seconds))


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

            self._is_running = True
            try:
                result = await asyncio.to_thread(_run_awning_agent, cfg.ai)
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
