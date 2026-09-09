"""Real Telegram Bot API smoke test for the Stage 3 test bot."""

import asyncio
import os
from pathlib import Path

import pytest
from telegram import Bot
from telegram.error import TelegramError

from stage_3.config import ConfigError, load_config


def _live_config():
    path = Path(os.environ.get("JOBSEARCH_STAGE3_ENV", ".env.stage3")).resolve()
    if not path.is_file():
        pytest.skip("Stage 3 Telegram credentials are not configured")
    try:
        return load_config(path=path, environ={})
    except ConfigError as exc:
        pytest.skip(f"Stage 3 Telegram configuration is unavailable: {type(exc).__name__}")


def test_real_telegram_send_message_reaches_configured_chat():
    config = _live_config()

    async def send():
        async with Bot(config.bot_token) as bot:
            return await bot.send_message(
                chat_id=config.chat_id,
                text="🧪 Stage 3 real-network integration test",
            )

    try:
        message = asyncio.run(send())
    except TelegramError as exc:
        pytest.fail(f"Telegram send failed: {type(exc).__name__}")

    assert message.message_id > 0
    assert message.chat.id == config.chat_id
    assert message.text == "🧪 Stage 3 real-network integration test"
