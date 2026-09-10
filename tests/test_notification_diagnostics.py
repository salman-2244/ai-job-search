"""Focused regressions for observable Telegram notification delivery."""

from __future__ import annotations

import logging

from stage_3.diagnostics import (
    DiagnosticFileHandler,
    callable_reference,
    prepare_telegram_text,
    safe_preview,
)


def test_safe_preview_redacts_tokens_and_bounds_text():
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
    preview = safe_preview("delivery " + token + "\n" + "x" * 200, limit=80)

    assert token not in preview
    assert "<redacted-token>" in preview
    assert "\n" not in preview
    assert len(preview) <= 80


def test_callable_reference_never_includes_closure_repr():
    secret = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"

    def callback(_text):
        return secret

    reference = callable_reference(callback)
    assert "callback" in reference
    assert secret not in reference


def test_diagnostic_handler_writes_bot_and_active_pipeline_logs(tmp_path):
    pipeline_log = tmp_path / "date" / "run" / "pipeline.log"
    handler = DiagnosticFileHandler(tmp_path, lambda: pipeline_log)
    handler.setFormatter(logging.Formatter("%(message)s"))
    record = logging.LogRecord("stage_3", logging.INFO, __file__, 1,
                               "telegram delivery succeeded", (), None)

    handler.emit(record)

    assert "delivery succeeded" in (tmp_path / "stage3-bot.log").read_text()
    assert "delivery succeeded" in pipeline_log.read_text()
    assert (tmp_path / "stage3-bot.log").stat().st_mode & 0o777 == 0o600


def test_diagnostic_handler_narrows_existing_log_permissions(tmp_path):
    bot_log = tmp_path / "stage3-bot.log"
    bot_log.write_text("existing\n")
    bot_log.chmod(0o644)
    handler = DiagnosticFileHandler(tmp_path, lambda: None)
    handler.setFormatter(logging.Formatter("%(message)s"))
    record = logging.LogRecord("stage_3", logging.INFO, __file__, 1,
                               "secure append", (), None)

    handler.emit(record)

    assert bot_log.stat().st_mode & 0o777 == 0o600


def test_prepare_telegram_text_enforces_api_limit():
    text = prepare_telegram_text("<unsafe>" * 1000)
    assert len(text) <= 4096
    assert text.endswith("[truncated by Stage 3]")
