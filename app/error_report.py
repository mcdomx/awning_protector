"""Structured error reporting for the AI deployment pipeline.

Implements the error-report shape defined in the project error-reporting rule: a
machine-readable JSON object emitted to the logging sink whenever an AI pipeline
stage or tool fails, instead of swallowing the error or surfacing an opaque string.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Error codes (SCREAMING_SNAKE_CASE), reused across pipeline call sites.
PARSE_ERROR = "PARSE_ERROR"
VALIDATION_FAILED = "VALIDATION_FAILED"
DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"

# Keys whose values are redacted from input snapshots (case-insensitive substring match).
_SENSITIVE_KEYS = ("password", "token", "key", "secret", "credential", "api_key")
_MAX_STR_LEN = 2000


def _sanitize(value: Any) -> Any:
    """Apply the snapshot sanitization rules: redact sensitive keys, truncate long
    strings, and omit binary data. Recurses through dicts and lists."""
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, val in value.items():
            if any(s in str(key).lower() for s in _SENSITIVE_KEYS):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize(val)
        return sanitized
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return "[BINARY_OMITTED]"
    if isinstance(value, str) and len(value) > _MAX_STR_LEN:
        return value[:_MAX_STR_LEN] + "...[TRUNCATED]"
    return value


def build_error_report(
    *,
    error_code: str,
    message: str,
    task_id: str,
    agent_id: str,
    retry_eligible: bool,
    input_snapshot: Optional[Dict[str, Any]] = None,
    suggested_action: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble a structured error report. Adds a UTC ISO-8601 ``occurred_at`` and
    runs ``input_snapshot`` through the sanitizer. ``suggested_action`` is omitted
    when not provided."""
    report: Dict[str, Any] = {
        "error_code": error_code,
        "message": message,
        "task_id": task_id,
        "agent_id": agent_id,
        "input_snapshot": _sanitize(input_snapshot or {}),
        "retry_eligible": retry_eligible,
        "occurred_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if suggested_action is not None:
        report["suggested_action"] = suggested_action
    return report


def emit_error_report(**kwargs: Any) -> Dict[str, Any]:
    """Build a report and emit it to the logging sink as a single-line JSON object.
    Returns the report dict so callers can also reuse it (e.g. in a tool result)."""
    report = build_error_report(**kwargs)
    logger.error(json.dumps(report))
    return report
