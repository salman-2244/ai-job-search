"""Secret-safe diagnostics for the Stage 3 Telegram delivery path."""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Callable

LOGGER_NAME = "stage_3"
BOT_LOG_NAME = "stage3-bot.log"
TELEGRAM_TEXT_LIMIT = 4096

_TOKEN_RE = re.compile(r"(?<!\d)\d{5,}:[A-Za-z0-9_-]{20,}")


def safe_preview(value: object, limit: int = 120) -> str:
    """Return a single-line preview with Telegram-shaped tokens removed."""
    text = " ".join(str(value).split())
    text = _TOKEN_RE.sub("<redacted-token>", text)
    if len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def callable_reference(callback: object) -> str:
    """Describe a callback without repr-ing closures or captured secrets."""
    module = getattr(callback, "__module__", type(callback).__module__)
    name = getattr(callback, "__qualname__", type(callback).__qualname__)
    return f"{module}.{name}"


def prepare_telegram_text(text: str) -> str:
    """Keep plain-text operator notices within Telegram's hard message limit."""
    if len(text) <= TELEGRAM_TEXT_LIMIT:
        return text
    suffix = "\n… [truncated by Stage 3]"
    return text[: TELEGRAM_TEXT_LIMIT - len(suffix)] + suffix


class DiagnosticFileHandler(logging.Handler):
    """Write diagnostics to the bot log and the active run's pipeline log."""

    def __init__(self, root: Path, active_log: Callable[[], Path | None]):
        super().__init__()
        self.root = Path(root)
        self.active_log = active_log
        self._write_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record) + "\n"
        targets = [self.root / BOT_LOG_NAME]
        try:
            active = self.active_log()
        except Exception:
            active = None
        if active is not None and active not in targets:
            targets.append(active)
        with self._write_lock:
            for target in targets:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                    os.fchmod(fd, 0o600)
                    with os.fdopen(fd, "a", encoding="utf-8") as stream:
                        stream.write(line)
                except OSError:
                    continue


def configure_diagnostics(root: Path, active_log: Callable[[], Path | None]) -> logging.Logger:
    """Configure one console and one durable handler, idempotently."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    if not any(getattr(handler, "_stage3_console", False) for handler in logger.handlers):
        console = logging.StreamHandler()
        console._stage3_console = True  # type: ignore[attr-defined]
        console.setFormatter(formatter)
        logger.addHandler(console)
    for handler in list(logger.handlers):
        if getattr(handler, "_stage3_file", False):
            logger.removeHandler(handler)
            handler.close()
    durable = DiagnosticFileHandler(root, active_log)
    durable._stage3_file = True  # type: ignore[attr-defined]
    durable.setFormatter(formatter)
    logger.addHandler(durable)
    return logger
