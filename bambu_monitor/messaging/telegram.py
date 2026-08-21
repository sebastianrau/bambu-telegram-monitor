from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Dict, Optional, TYPE_CHECKING

import requests

from ..common import LOG, STOP
from .base import MessageClient

if TYPE_CHECKING:
    from ..printer import PrinterRuntime

class TelegramClient(MessageClient):
    provider = "telegram"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.token = str(cfg["bot_token"])
        self.chat_id = str(cfg["chat_id"])
        self.timeout = int(cfg.get("timeout_seconds", 30))
        self.disable_notification = bool(cfg.get("disable_notification", False))
        self.protect_content = bool(cfg.get("protect_content", False))
        self.caption_template = cfg.get(
            "caption",
            "🖨️ {printer}: {milestone} ({progress}%)"
        )
        self.base = f"https://api.telegram.org/bot{self.token}"

    def send_image(self, image_path: Path, printer_name: str, milestone: str, progress: int):
        url = f"{self.base}/sendPhoto"
        caption = self.caption_template.format(
            printer=printer_name,
            milestone=milestone,
            progress=progress,
        )

        data = {
            "chat_id": self.chat_id,
            "caption": caption,
            "disable_notification": str(self.disable_notification).lower(),
            "protect_content": str(self.protect_content).lower(),
        }

        with image_path.open("rb") as fh:
            response = requests.post(
                url,
                data=data,
                files={"photo": (image_path.name, fh, "image/jpeg")},
                timeout=self.timeout,
            )

        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError(f"Telegram API error: {payload}")

        LOG.info("Telegram sent: %s / %s", printer_name, milestone)

    def send_text(self, chat_id: str, message: str):
        response = requests.post(
            f"{self.base}/sendMessage",
            data={"chat_id": str(chat_id), "text": message},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError(f"Telegram API error: {payload}")


class TelegramCommandPoller:
    COMMANDS = {"/snapshot", "/snapshop"}

    def __init__(self, cfg: dict, telegram: TelegramClient,
                 runtimes: list[PrinterRuntime]):
        self.cfg = cfg
        self.telegram = telegram
        self.runtimes = runtimes
        self.enabled = bool(cfg.get("commands_enabled", True))
        self.poll_timeout = int(cfg.get("command_poll_timeout_seconds", 20))
        self.cooldown = float(cfg.get("command_cooldown_seconds", 10))
        self.thread: Optional[threading.Thread] = None
        self.offset: Optional[int] = None
        self.last_command_at: Dict[str, float] = {}

    def start(self):
        if not self.enabled:
            LOG.info("Telegram command polling disabled")
            return
        self.thread = threading.Thread(
            target=self._run,
            name="telegram-commands",
            daemon=True,
        )
        self.thread.start()
        LOG.info("Telegram commands enabled: /snapshot, /snapshop")

    def stop(self):
        if self.thread:
            # A requests-based Telegram long poll cannot be cancelled from
            # another thread. The poller is a daemon, so do not make shutdown
            # wait for the configured long-poll/HTTP timeout to expire.
            self.thread.join(timeout=1)
            if self.thread.is_alive():
                LOG.debug("Telegram command poll still active; leaving daemon thread")

    def _run(self):
        while not STOP.is_set():
            try:
                self._discard_pending_updates()
                break
            except Exception:
                LOG.exception(
                    "Could not initialize Telegram command polling; retrying in 5 seconds"
                )
                if STOP.wait(5):
                    return

        while not STOP.is_set():
            try:
                params = {
                    "timeout": self.poll_timeout,
                    "allowed_updates": json.dumps(["message", "channel_post"]),
                }
                if self.offset is not None:
                    params["offset"] = self.offset
                response = requests.get(
                    f"{self.telegram.base}/getUpdates",
                    params=params,
                    timeout=self.poll_timeout + 10,
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok", False):
                    raise RuntimeError(
                        f"Telegram getUpdates error: {payload.get('description', payload)}"
                    )
                for update in payload.get("result", []):
                    update_id = update.get("update_id")
                    if update_id is not None:
                        self.offset = int(update_id) + 1
                    self.handle_update(update)
            except Exception:
                if not STOP.is_set():
                    LOG.exception("Telegram command polling failed; retrying in 5 seconds")
                if STOP.wait(5):
                    return

    def _discard_pending_updates(self):
        response = requests.get(
            f"{self.telegram.base}/getUpdates",
            params={"offset": -1, "limit": 1, "timeout": 0},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError(
                f"Telegram getUpdates error: {payload.get('description', payload)}"
            )
        updates = payload.get("result", [])
        if updates and updates[-1].get("update_id") is not None:
            self.offset = int(updates[-1]["update_id"]) + 1

    def handle_update(self, update: dict) -> bool:
        message = update.get("message") or update.get("channel_post")
        if not isinstance(message, dict):
            return False
        chat = message.get("chat")
        if not isinstance(chat, dict) or "id" not in chat:
            return False
        text = str(message.get("text", "")).strip()
        if not text:
            return False
        parts = text.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()
        if command not in self.COMMANDS:
            return False

        chat_id = str(chat["id"])
        if chat_id != self.telegram.chat_id:
            LOG.warning("Ignoring Telegram snapshot command from unauthorized chat %s", chat_id)
            return False

        now = time.monotonic()
        last = self.last_command_at.get(chat_id)
        if last is not None and now - last < self.cooldown:
            remaining = max(1, int(self.cooldown - (now - last) + 0.999))
            self.telegram.send_text(
                chat_id,
                f"Bitte {remaining} Sekunde(n) bis zum nächsten Snapshot warten.",
            )
            return True

        selector = parts[1].strip() if len(parts) > 1 else ""
        targets = self._select_runtimes(selector)
        if not targets:
            available = ", ".join(runtime.name for runtime in self.runtimes)
            self.telegram.send_text(
                chat_id,
                f"Drucker nicht gefunden. Verfügbar: {available or 'keine'}",
            )
            return True
        if selector and len(targets) > 1:
            matches = ", ".join(
                f"{runtime.name} ({runtime.serial})" for runtime in targets
            )
            self.telegram.send_text(
                chat_id,
                f"Auswahl ist nicht eindeutig. Treffer: {matches}",
            )
            return True

        queued = sum(1 for runtime in targets if runtime.request_manual_snapshot())
        if queued == 0:
            self.telegram.send_text(
                chat_id,
                "Snapshot konnte nicht eingereiht werden. Bitte später erneut versuchen.",
            )
            return True

        self.last_command_at[chat_id] = now
        LOG.info(
            "Telegram snapshot command accepted for %d printer(s) from authorized chat",
            queued,
        )
        return True

    def _select_runtimes(self, selector: str) -> list[PrinterRuntime]:
        if not selector:
            return list(self.runtimes)
        wanted = selector.casefold()
        exact = [
            runtime for runtime in self.runtimes
            if runtime.name.casefold() == wanted or runtime.serial.casefold() == wanted
        ]
        if exact:
            return exact
        return [
            runtime for runtime in self.runtimes
            if wanted in runtime.name.casefold()
            or runtime.serial.casefold().startswith(wanted)
        ]


def extract_telegram_chats(updates) -> dict:
    chats = {}

    def visit(value):
        if isinstance(value, dict):
            chat = value.get("chat")
            if isinstance(chat, dict) and "id" in chat:
                chat_id = str(chat["id"])
                chats[chat_id] = {
                    "type": str(chat.get("type", "unknown")),
                    "name": str(
                        chat.get("title")
                        or " ".join(
                            part for part in (
                                chat.get("first_name"), chat.get("last_name")
                            ) if part
                        )
                        or chat.get("username")
                        or "unknown"
                    ),
                }
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(updates)
    return chats


def extract_telegram_id_requests(updates) -> dict:
    requests_by_chat = {}
    for update in updates if isinstance(updates, list) else []:
        if not isinstance(update, dict):
            continue
        for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
            message = update.get(key)
            if not isinstance(message, dict):
                continue
            text = str(message.get("text", "")).strip()
            command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
            chat = message.get("chat")
            if command == "/id" and isinstance(chat, dict) and "id" in chat:
                requests_by_chat[str(chat["id"])] = chat
    return requests_by_chat


def find_telegram_chat_ids(cfg: dict, wait_seconds: int,
                           include_old: bool = False) -> bool:
    messaging = cfg.get("messaging")
    telegram_cfg = (
        messaging.get("telegram") if isinstance(messaging, dict) else None
    ) or cfg.get("telegram", {})
    token = str(telegram_cfg["bot_token"])
    base = f"https://api.telegram.org/bot{token}"
    try:
        offset = None
        payload = None
        if not include_old:
            # Fetch only the newest pending update and advance past it. Telegram
            # treats a higher offset as confirmation, so the next long poll
            # waits for a message sent after this lookup was started.
            pending_response = requests.get(
                f"{base}/getUpdates",
                params={"offset": -1, "limit": 1, "timeout": 0},
                timeout=10,
            )
            pending = pending_response.json()
            if not pending.get("ok", False):
                payload = pending
            else:
                results = pending.get("result", [])
                if results:
                    offset = int(results[-1]["update_id"]) + 1
                payload = None

        if payload is None:
            LOG.info(
                "Waiting up to %d seconds. Send /id to the bot now.",
                wait_seconds,
            )
            params = {"timeout": wait_seconds, "allowed_updates": json.dumps([])}
            if offset is not None:
                params["offset"] = offset
            response = requests.get(
                f"{base}/getUpdates",
                params=params,
                timeout=wait_seconds + 10,
            )
            payload = response.json()
    except Exception:
        LOG.exception("Could not query Telegram updates")
        return False

    if not payload.get("ok", False):
        LOG.error("Telegram getUpdates failed: %s", payload.get("description", payload))
        try:
            webhook = requests.get(f"{base}/getWebhookInfo", timeout=10).json()
            url = webhook.get("result", {}).get("url") if webhook.get("ok") else None
            if url:
                LOG.error(
                    "A webhook is active. getUpdates cannot be used until it is removed: %s",
                    url,
                )
        except Exception:
            LOG.debug("Could not query Telegram webhook information", exc_info=True)
        return False

    updates = payload.get("result", [])
    id_requests = extract_telegram_id_requests(updates)
    if not id_requests:
        LOG.error(
            "No /id command found. Run lookup mode again and send /id to the bot while it is waiting."
        )
        return False

    all_sent = True
    for chat_id, chat in id_requests.items():
        info = extract_telegram_chats([{"message": {"chat": chat}}])[chat_id]
        LOG.info(
            "Telegram chat found: id=%s type=%s name=%s",
            chat_id, info["type"], info["name"],
        )
        try:
            reply = requests.post(
                f"{base}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "text": f"Your Telegram chat ID is: {chat_id}",
                },
                timeout=10,
            ).json()
            if not reply.get("ok", False):
                LOG.error(
                    "Could not send chat ID to %s: %s",
                    chat_id, reply.get("description", reply),
                )
                all_sent = False
            else:
                LOG.info("Telegram chat ID sent back to chat %s", chat_id)
        except Exception:
            LOG.exception("Could not send chat ID back to chat %s", chat_id)
            all_sent = False
    return all_sent
