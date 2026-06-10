import json
import logging

from app.error_report import (
    DEPENDENCY_UNAVAILABLE,
    _sanitize,
    build_error_report,
    emit_error_report,
)


# ── _sanitize ─────────────────────────────────────────────────────────────────

def test_sanitize_redacts_sensitive_keys_including_nested():
    data = {
        "api_key": "abc",
        "Authorization_token": "xyz",
        "nested": {"password": "p", "safe": "keep"},
        "list": [{"secret": "s"}, "plain"],
    }
    result = _sanitize(data)
    assert result["api_key"] == "[REDACTED]"
    assert result["Authorization_token"] == "[REDACTED]"
    assert result["nested"]["password"] == "[REDACTED]"
    assert result["nested"]["safe"] == "keep"
    assert result["list"][0]["secret"] == "[REDACTED]"
    assert result["list"][1] == "plain"


def test_sanitize_truncates_long_strings():
    long = "a" * 2500
    result = _sanitize({"blob": long})
    assert result["blob"].endswith("...[TRUNCATED]")
    assert len(result["blob"]) == 2000 + len("...[TRUNCATED]")


def test_sanitize_omits_binary():
    result = _sanitize({"payload": b"\x00\x01"})
    assert result["payload"] == "[BINARY_OMITTED]"


# ── build_error_report ──────────────────────────────────────────────────────────

def test_build_error_report_has_required_fields_and_timestamp():
    report = build_error_report(
        error_code=DEPENDENCY_UNAVAILABLE,
        message="boom",
        task_id="t1",
        agent_id="wind-worker",
        retry_eligible=True,
        input_snapshot={"token": "secret-value", "x": 1},
        suggested_action="retry",
    )
    assert report["error_code"] == DEPENDENCY_UNAVAILABLE
    assert report["task_id"] == "t1"
    assert report["agent_id"] == "wind-worker"
    assert report["retry_eligible"] is True
    assert report["suggested_action"] == "retry"
    assert report["input_snapshot"]["token"] == "[REDACTED]"
    assert report["input_snapshot"]["x"] == 1
    assert report["occurred_at"].endswith("Z")


def test_build_error_report_omits_suggested_action_when_none():
    report = build_error_report(
        error_code="PARSE_ERROR",
        message="m",
        task_id="t",
        agent_id="a",
        retry_eligible=False,
    )
    assert "suggested_action" not in report
    assert report["input_snapshot"] == {}


# ── emit_error_report ────────────────────────────────────────────────────────────

def test_emit_error_report_logs_single_line_json(caplog):
    with caplog.at_level(logging.ERROR, logger="app.error_report"):
        report = emit_error_report(
            error_code="VALIDATION_FAILED",
            message="bad",
            task_id="t",
            agent_id="a",
            retry_eligible=True,
        )
    assert len(caplog.records) == 1
    line = caplog.records[0].getMessage()
    assert "\n" not in line  # single line
    assert json.loads(line) == report
