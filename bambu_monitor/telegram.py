"""Compatibility exports for the Telegram messaging adapter."""

from .messaging.telegram import (
    TelegramClient,
    TelegramCommandPoller,
    extract_telegram_chats,
    extract_telegram_id_requests,
    find_telegram_chat_ids,
)

__all__ = [
    "TelegramClient", "TelegramCommandPoller", "extract_telegram_chats",
    "extract_telegram_id_requests", "find_telegram_chat_ids",
]
