"""Production-shaped Telegram notification delivery tests."""

import asyncio
import concurrent.futures
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import Bot
from telegram.error import TelegramError

from stage_3.bot import _delivery_completed, _wire_notifications
from stage_3.config import ConfigError, Stage3Config, load_config
from stage_3.diagnostics import TELEGRAM_TEXT_LIMIT, prepare_telegram_text


class CapturingOrchestrator:
    def set_notify(self, notify):
        self.notify = notify


class AsyncBot:
    def __init__(self, result=None, error=None):
        self.result = result or SimpleNamespace(message_id=77)
        self.error = error
        self.calls = []

    async def send_message(self, chat_id, text):
        self.calls.append((chat_id, text))
        if self.error is not None:
            raise self.error
        return self.result


async def _exercise_callback(bot, texts):
    application = SimpleNamespace(bot=bot)
    orchestrator = CapturingOrchestrator()
    config = Stage3Config(
        bot_token="123456:synthetic-token-value",
        chat_id=42,
        allowed_user_ids=(42,),
    )
    _wire_notifications(application, orchestrator, config, asyncio.get_running_loop())
    futures = [orchestrator.notify(text) for text in texts]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return bot.calls, futures


def test_notification_adapter_delivers_terminal_outcomes_and_limits_text():
    texts = [
        "✅ Run run-success terminal: complete — documents saved to disk.",
        "❌ Run run-failure terminal: failed (exit 1, class pipeline).",
        "⏹ Run run-cancelled terminal: cancelled.",
        "<untrusted>" + "x" * TELEGRAM_TEXT_LIMIT,
    ]

    calls, futures = asyncio.run(_exercise_callback(AsyncBot(), texts))

    assert [chat_id for chat_id, _ in calls] == [42] * len(texts)
    assert all(isinstance(future, concurrent.futures.Future) for future in futures)
    assert all(future.done() and future.exception() is None for future in futures)
    assert [text for _, text in calls[:3]] == texts[:3]
    assert len(calls[-1][1]) == TELEGRAM_TEXT_LIMIT
    assert calls[-1][1].endswith("… [truncated by Stage 3]")


def test_delivery_completion_logs_observed_failure_without_raising(caplog):
    future = concurrent.futures.Future()
    future.set_exception(RuntimeError("synthetic invalid chat"))

    with caplog.at_level(logging.ERROR, logger="stage_3.bot"):
        _delivery_completed(future, chat_id=-1, preview="safe preview")

    assert "telegram api failure" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text


def _live_config():
    path = Path(os.environ.get("JOBSEARCH_STAGE3_ENV", ".env.stage3")).resolve()
    if not path.is_file():
        pytest.skip("Stage 3 Telegram credentials are not configured")
    try:
        return load_config(path=path, environ={})
    except ConfigError as exc:
        pytest.skip(f"Stage 3 Telegram configuration is unavailable: {type(exc).__name__}")


def test_real_notification_terminal_messages_reach_configured_chat():
    config = _live_config()
    messages = [
        "▶️ Run integration-live started.",
        "✅ Run integration-live terminal: complete — documents saved to disk.",
        "❌ Run integration-live terminal: failed (exit 1, class synthetic).",
        "⏹ Run integration-live terminal: cancelled.",
        prepare_telegram_text("HTML safety: <job> & <company>"),
        prepare_telegram_text("Length test: " + "x" * TELEGRAM_TEXT_LIMIT),
    ]

    async def send_all():
        async with Bot(config.bot_token) as bot:
            return [
                await bot.send_message(chat_id=config.chat_id, text=text)
                for text in messages
            ]

    try:
        sent = asyncio.run(send_all())
    except TelegramError as exc:
        pytest.fail(f"Telegram notification delivery failed: {type(exc).__name__}")

    assert all(message.message_id > 0 for message in sent)
    assert all(message.chat.id == config.chat_id for message in sent)
    assert len(sent[-1].text) == TELEGRAM_TEXT_LIMIT


def test_real_invalid_chat_delivery_fails_without_stopping_followup_delivery():
    config = _live_config()

    async def exercise():
        async with Bot(config.bot_token) as bot:
            invalid_error = None
            try:
                await bot.send_message(chat_id=-1, text="invalid destination test")
            except TelegramError as exc:
                invalid_error = type(exc).__name__
            followup = await bot.send_message(
                chat_id=config.chat_id,
                text="✅ Stage 3 recovered after invalid-chat test",
            )
            return invalid_error, followup

    error_type, followup = asyncio.run(exercise())

    assert error_type is not None
    assert followup.chat.id == config.chat_id
    assert followup.message_id > 0
