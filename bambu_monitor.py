#!/usr/bin/env python3
import argparse
import json
import logging
import os
import queue
import signal
import socket
import ssl
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt
import requests
import yaml


LOG = logging.getLogger("bambu-monitor")
STOP = threading.Event()


def deep_merge(dst: dict, src: dict) -> dict:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


class PersistentState:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self):
        try:
            self.data = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.data = {}
        except Exception:
            LOG.exception("Could not load state file %s", self.path)
            self.data = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def printer(self, serial: str) -> dict:
        with self.lock:
            return self.data.setdefault(serial, {})

    def update_printer(self, serial: str, values: dict):
        with self.lock:
            entry = self.data.setdefault(serial, {})
            entry.update(values)
            self.save()


class TelegramClient:
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


def capture_p1s_snapshot(host: str, access_code: str, output: Path,
                         timeout: float = 10.0, warmup_frames: int = 2):
    """
    P1/A1 camera protocol:
      TLS TCP/6000
      80-byte authentication packet
      then repeating 16-byte frame header + JPEG payload.

    Auth packet:
      uint32_le size = 0x40
      uint32_le type = 0x3000
      username "bblp" at offset 16
      access code at offset 48
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    raw = socket.create_connection((host, 6000), timeout=timeout)
    sock = ctx.wrap_socket(raw, server_hostname=host)
    sock.settimeout(timeout)

    try:
        auth = bytearray(80)
        struct.pack_into("<I", auth, 0, 0x40)
        struct.pack_into("<I", auth, 4, 0x3000)
        auth[16:16 + 4] = b"bblp"
        code = access_code.encode("utf-8")[:31]
        auth[48:48 + len(code)] = code
        sock.sendall(auth)

        deadline = time.time() + timeout
        valid_frames = 0
        while time.time() < deadline:
            header = recv_exact(sock, 16)
            payload_size = struct.unpack_from("<I", header, 0)[0]

            # Reject implausible frame sizes before allocating.
            if payload_size <= 0 or payload_size > 20 * 1024 * 1024:
                raise RuntimeError(f"Invalid camera payload size: {payload_size}")

            payload = recv_exact(sock, payload_size)

            # JPEG SOI/EOI. Ignore non-JPEG protocol/error frames.
            if payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9"):
                if valid_frames < warmup_frames:
                    valid_frames += 1
                    LOG.debug(
                        "Discarding buffered camera frame %d/%d from %s",
                        valid_frames, warmup_frames, host,
                    )
                    continue
                output.write_bytes(payload)
                return

        raise TimeoutError("No JPEG frame received from P1S camera")
    finally:
        try:
            sock.close()
        except Exception:
            pass


def recv_exact(sock: ssl.SSLSocket, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise ConnectionError("Camera connection closed")
        buf.extend(chunk)
    return bytes(buf)


@dataclass
class PrinterRuntime:
    cfg: dict
    telegram: TelegramClient
    state_store: PersistentState
    snapshot_dir: Path
    mqtt_state: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    client: Optional[mqtt.Client] = None
    event_queue: queue.Queue = field(init=False)
    worker: Optional[threading.Thread] = field(default=None, init=False)

    def __post_init__(self):
        self.event_queue = queue.Queue(
            maxsize=int(self.cfg.get("event_queue_size", 16))
        )

    @property
    def serial(self):
        return str(self.cfg["serial"])

    @property
    def name(self):
        return self.cfg.get("name", self.serial)

    @property
    def host(self):
        return self.cfg["host"]

    @property
    def access_code(self):
        return str(self.cfg["access_code"])

    def run(self):
        self.worker = threading.Thread(
            target=self._event_worker,
            name=f"bambu-events-{self.serial[-8:]}",
            daemon=True,
        )
        self.worker.start()

        client_id = f"bambu-tg-{self.serial[-8:]}-{os.getpid()}"
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        self.client.username_pw_set("bblp", self.access_code)

        # Printer uses a self-signed TLS certificate.
        self.client.tls_set(cert_reqs=ssl.CERT_NONE)
        self.client.tls_insecure_set(True)

        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

        reconnect_min = int(self.cfg.get("mqtt_reconnect_min_seconds", 2))
        reconnect_max = int(self.cfg.get("mqtt_reconnect_max_seconds", 60))
        self.client.reconnect_delay_set(reconnect_min, reconnect_max)

        LOG.info("[%s] connecting MQTT %s:8883", self.name, self.host)
        self.client.connect_async(self.host, 8883, keepalive=30)
        self.client.loop_start()

    def stop(self):
        if self.client:
            self.client.disconnect()
            self.client.loop_stop()
        if self.worker:
            try:
                self.event_queue.put(None, timeout=5)
            except queue.Full:
                LOG.warning("[%s] event worker did not stop cleanly", self.name)
            self.worker.join(timeout=5)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            LOG.error("[%s] MQTT connection failed: %s", self.name, reason_code)
            return

        topic = f"device/{self.serial}/report"
        LOG.info("[%s] MQTT connected, subscribing %s", self.name, topic)
        client.subscribe(topic, qos=0)

        # Ask the printer for a complete state report.
        request_topic = f"device/{self.serial}/request"
        request = {
            "pushing": {
                "sequence_id": "0",
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        }
        client.publish(request_topic, json.dumps(request), qos=0)

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if not STOP.is_set():
            LOG.warning("[%s] MQTT disconnected: %s", self.name, reason_code)

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            LOG.exception("[%s] invalid MQTT JSON", self.name)
            return

        with self.lock:
            try:
                deep_merge(self.mqtt_state, payload)
                print_state = self.mqtt_state.get("print", {})
                self.evaluate(print_state)
            except Exception:
                # Never let persistence/configuration errors terminate Paho's
                # network thread; keep the connection alive and report them.
                LOG.exception("[%s] MQTT message processing failed", self.name)

    def evaluate(self, p: dict):
        state = str(p.get("gcode_state", "")).upper()
        progress = as_int(p.get("mc_percent"))
        layer = as_int(p.get("layer_num"))
        total_layers = as_int(p.get("total_layer_num"))
        task_id = str(
            p.get("task_id")
            or p.get("subtask_id")
            or p.get("gcode_file")
            or p.get("subtask_name")
            or ""
        )

        if not state and progress is None and layer is None:
            return

        persisted = self.state_store.printer(self.serial)
        previous_task = persisted.get("task_id")
        had_job_history = bool(
            persisted.get("last_gcode_state")
            or persisted.get("task_id")
            or "last_progress" in persisted
        )

        # Detect a new print. Prefer Bambu task/subtask ID; fall back to progress reset.
        new_job = False
        if task_id and previous_task and task_id != previous_task:
            new_job = True
        elif task_id and not previous_task and state in {"RUNNING", "PREPARE", "PAUSE"}:
            new_job = True
        elif (
            progress is not None
            and progress <= 2
            and persisted.get("last_progress", 0) > 10
            and state in {"RUNNING", "PREPARE"}
        ):
            new_job = True

        if new_job:
            LOG.info("[%s] new print detected: %s", self.name, task_id or "(unknown)")
            self.state_store.update_printer(
                self.serial,
                {
                    "task_id": task_id,
                    "started_sent": False,
                    "layer1_sent": False,
                    "progress50_sent": False,
                    "finished_sent": False,
                    "pause_sent": False,
                    "failed_sent": False,
                    "last_progress": progress or 0,
                    "last_gcode_state": "",
                },
            )
            persisted = self.state_store.printer(self.serial)

        # Track task ID even if it arrives after the first packets.
        if task_id and persisted.get("task_id") != task_id:
            self.state_store.update_printer(self.serial, {"task_id": task_id})
            persisted = self.state_store.printer(self.serial)

        if progress is not None:
            self.state_store.update_printer(self.serial, {"last_progress": progress})

        # Remember state transitions. P1 sends differential updates, therefore
        # this is evaluated against our merged MQTT state.
        previous_state = str(persisted.get("last_gcode_state", "")).upper()

        # On the first observation after installation/reconfiguration, terminal
        # states are treated as a baseline. This avoids sending a stale FINISH,
        # FAILED or PAUSE notification simply because the service started.
        if not previous_state and state in {"IDLE", "FINISH", "FAILED", "PAUSE"}:
            baseline = {"last_gcode_state": state}
            if state == "FINISH":
                baseline["finished_sent"] = True
            elif state == "FAILED":
                baseline["failed_sent"] = True
            elif state == "PAUSE":
                baseline["pause_sent"] = True
            self.state_store.update_printer(self.serial, baseline)
            return

        if state and state != previous_state:
            self.state_store.update_printer(self.serial, {"last_gcode_state": state})

        notifications = self.cfg.get("notifications", {})

        # Announce a newly detected job only when this printer already has a
        # persisted baseline. This avoids a false "started" message when the
        # monitor is installed or restarted in the middle of an active print.
        persisted = self.state_store.printer(self.serial)
        if (
            new_job
            and had_job_history
            and notifications.get("started", True)
            and state in {"PREPARE", "RUNNING"}
            and not persisted.get("started_sent", False)
        ):
            self.fire("started", "Druck gestartet", progress or 0, layer, total_layers)
            return

        # Finished layer 1. Restrict milestones to an active/paused job so that
        # reconnecting to an old FINISH state cannot create stale messages.
        if (
            notifications.get("layer1", True)
            and layer is not None
            and layer >= 2
            and not persisted.get("layer1_sent", False)
            and state in {"RUNNING", "PAUSE"}
        ):
            self.fire("layer1", "Layer 1 fertig", progress or 0, layer, total_layers)
            return

        persisted = self.state_store.printer(self.serial)
        if (
            notifications.get("progress50", True)
            and progress is not None
            and progress >= 50
            and not persisted.get("progress50_sent", False)
            and not persisted.get("finished_sent", False)
            and state in {"RUNNING", "PAUSE"}
        ):
            self.fire("progress50", "50 % erreicht", progress, layer, total_layers)
            return

        # Pause is sent once per pause transition. Resume clears the pause flag,
        # allowing a later independent pause to notify again.
        persisted = self.state_store.printer(self.serial)
        if state == "RUNNING" and previous_state == "PAUSE":
            self.state_store.update_printer(self.serial, {"pause_sent": False})
            persisted = self.state_store.printer(self.serial)

        if (
            notifications.get("pause", True)
            and state == "PAUSE"
            and previous_state != "PAUSE"
            and not persisted.get("pause_sent", False)
        ):
            self.fire("pause", "Druck pausiert", progress or 0, layer, total_layers)
            return

        # FINISH is the authoritative completion signal. We deliberately do not
        # send the final notification merely because mc_percent reached 100.
        persisted = self.state_store.printer(self.serial)
        if (
            notifications.get("finished", True)
            and state == "FINISH"
            and not persisted.get("finished_sent", False)
        ):
            self.fire("finished", "Druck fertig", progress if progress is not None else 100, layer, total_layers)
            return

        # Bambu uses FAILED for unsuccessful/aborted prints. The protocol does
        # not reliably distinguish a user cancel from every failure case.
        persisted = self.state_store.printer(self.serial)
        if (
            notifications.get("failed", True)
            and state == "FAILED"
            and not persisted.get("failed_sent", False)
        ):
            self.fire("failed", "Druck abgebrochen/fehlgeschlagen", progress or 0, layer, total_layers)

    def fire(self, milestone_key: str, milestone_label: str, progress: int,
             layer: Optional[int], total_layers: Optional[int]):
        flag = f"{milestone_key}_sent"

        # Reserve the event before queueing to avoid duplicate MQTT-triggered work.
        self.state_store.update_printer(self.serial, {flag: True})

        event = (milestone_key, milestone_label, progress, layer, total_layers)
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            self.state_store.update_printer(self.serial, {flag: False})
            LOG.error("[%s] event queue full, dropping %s", self.name, milestone_label)

    def _event_worker(self):
        while True:
            event = self.event_queue.get()
            try:
                if event is None:
                    return
                self._deliver_event(*event)
            finally:
                self.event_queue.task_done()

    def _deliver_event(self, milestone_key: str, milestone_label: str, progress: int,
                       layer: Optional[int], total_layers: Optional[int]):
        flag = f"{milestone_key}_sent"

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.name)
        output = self.snapshot_dir / safe_name / f"{timestamp}-{milestone_key}.jpg"

        LOG.info(
            "[%s] milestone %s, progress=%s layer=%s/%s",
            self.name, milestone_label, progress, layer, total_layers
        )

        attempts = int(self.cfg.get("delivery_attempts", 3))
        backoff = float(self.cfg.get("delivery_retry_seconds", 5))
        for attempt in range(1, attempts + 1):
            try:
                capture_p1s_snapshot(
                    self.host,
                    self.access_code,
                output,
                timeout=float(self.cfg.get("camera_timeout_seconds", 10)),
                warmup_frames=int(self.cfg.get("camera_warmup_frames", 2)),
            )
                LOG.info("[%s] snapshot saved: %s", self.name, output)
                self.telegram.send_image(output, self.name, milestone_label, progress)
                return
            except Exception:
                LOG.exception(
                    "[%s] milestone handling failed: %s (attempt %d/%d)",
                    self.name, milestone_label, attempt, attempts,
                )
                if attempt < attempts and not STOP.wait(backoff * (2 ** (attempt - 1))):
                    continue
                break

        # Allow a later MQTT report to enqueue a fresh delivery cycle.
        self.state_store.update_printer(self.serial, {flag: False})


def as_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_config(path: Path, require_telegram: bool = True,
                require_chat_id: bool = True) -> dict:
    cfg = yaml.safe_load(path.read_text())
    if not isinstance(cfg, dict):
        raise ValueError("Config must be a YAML mapping")
    printers = cfg.get("printers")
    if not isinstance(printers, list) or not printers:
        raise ValueError("At least one printer must be configured")
    for index, printer in enumerate(printers, start=1):
        if not isinstance(printer, dict):
            raise ValueError(f"Printer {index} must be a YAML mapping")
        if printer.get("enabled", True):
            for key in ("host", "serial", "access_code"):
                if not printer.get(key):
                    raise ValueError(f"Enabled printer {index} is missing {key}")
        for key in ("event_queue_size", "delivery_attempts"):
            if key in printer and int(printer[key]) < 1:
                raise ValueError(f"Printer {index} {key} must be at least 1")
        if "camera_warmup_frames" in printer and int(printer["camera_warmup_frames"]) < 0:
            raise ValueError(f"Printer {index} camera_warmup_frames must not be negative")
    if not any(printer.get("enabled", True) for printer in printers):
        raise ValueError("At least one printer must be enabled")

    for key in ("snapshot_retention_days", "snapshot_cleanup_interval_hours"):
        if key in cfg and float(cfg[key]) < 0:
            raise ValueError(f"{key} must not be negative")

    if require_telegram:
        telegram = cfg.get("telegram")
        if not isinstance(telegram, dict):
            raise ValueError("telegram must be a YAML mapping")
        required_keys = ("bot_token", "chat_id") if require_chat_id else ("bot_token",)
        for key in required_keys:
            if not telegram.get(key):
                raise ValueError(f"telegram is missing {key}")
    return cfg


def configure_logging(cfg: dict):
    level = getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


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
    token = str(cfg["telegram"]["bot_token"])
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


def clean_snapshots(snapshot_dir: Path, older_than: Optional[float] = None) -> int:
    requested = snapshot_dir.expanduser()
    if requested.is_symlink():
        raise ValueError(f"Refusing symlink snapshot directory: {requested}")

    target = requested.resolve()
    forbidden = {Path("/").resolve(), Path.home().resolve()}
    if target in forbidden:
        raise ValueError(f"Refusing unsafe snapshot directory: {target}")
    if not target.exists():
        LOG.info("Snapshot directory does not exist: %s", target)
        return 0
    if not target.is_dir():
        raise ValueError(f"Snapshot path is not a directory: {target}")

    removed = 0
    for current, dirnames, filenames in os.walk(target, topdown=False, followlinks=False):
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            if path.is_symlink():
                LOG.warning("Skipping snapshot symlink: %s", path)
                continue
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}:
                if older_than is not None and path.stat().st_mtime >= older_than:
                    continue
                path.unlink()
                removed += 1
                LOG.info("Deleted snapshot: %s", path)

        for dirname in dirnames:
            path = current_path / dirname
            if path.is_symlink():
                LOG.warning("Skipping snapshot directory symlink: %s", path)
                continue
            try:
                path.rmdir()
                LOG.info("Removed empty snapshot directory: %s", path)
            except OSError:
                # Preserve directories containing non-snapshot files.
                pass

    LOG.info("Snapshot cleanup completed: %d image(s) deleted from %s", removed, target)
    return removed


def run_scheduled_snapshot_cleanup(cfg: dict, snapshot_dir: Path) -> int:
    retention_days = float(cfg.get("snapshot_retention_days", 7))
    if retention_days <= 0:
        LOG.debug("Automatic snapshot cleanup is disabled")
        return 0
    cutoff = time.time() - retention_days * 24 * 60 * 60
    LOG.info(
        "Cleaning snapshots older than %.2f day(s) from %s",
        retention_days, snapshot_dir,
    )
    return clean_snapshots(snapshot_dir, older_than=cutoff)


def test_printer_mqtt(printer: dict, timeout: float) -> bool:
    name = printer.get("name", printer["serial"])
    serial = str(printer["serial"])
    connected = threading.Event()
    report_received = threading.Event()
    connection_error = []

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"bambu-test-{serial[-8:]}-{os.getpid()}",
        clean_session=True,
    )
    client.username_pw_set("bblp", str(printer["access_code"]))
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)

    report_topic = f"device/{serial}/report"
    request_topic = f"device/{serial}/request"

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            connection_error.append(str(reason_code))
            connected.set()
            return
        LOG.info("[%s] MQTT connected", name)
        client.subscribe(report_topic, qos=0)
        request = {
            "pushing": {
                "sequence_id": "0",
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        }
        client.publish(request_topic, json.dumps(request), qos=0)
        connected.set()

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            if isinstance(payload, dict):
                report_received.set()
        except Exception:
            LOG.warning("[%s] MQTT test received invalid JSON", name)

    client.on_connect = on_connect
    client.on_message = on_message

    LOG.info("[%s] testing MQTT connection to %s:8883", name, printer["host"])
    try:
        client.connect(str(printer["host"]), 8883, keepalive=30)
        client.loop_start()
        if not connected.wait(timeout):
            LOG.error("[%s] MQTT connection timed out after %.1f seconds", name, timeout)
            return False
        if connection_error:
            LOG.error("[%s] MQTT connection rejected: %s", name, connection_error[0])
            return False
        if not report_received.wait(timeout):
            LOG.error("[%s] MQTT connected, but no status report was received", name)
            return False
        LOG.info("[%s] MQTT status report received", name)
        return True
    except Exception:
        LOG.exception("[%s] MQTT connection test failed", name)
        return False
    finally:
        try:
            client.disconnect()
            client.loop_stop()
        except Exception:
            pass


def run_bambu_connection_test(cfg: dict, timeout: float, output_dir: Path) -> bool:
    snapshot_dir = output_dir.expanduser().resolve()
    printers = [p for p in cfg["printers"] if p.get("enabled", True)]
    all_ok = True

    for printer in printers:
        name = printer.get("name", str(printer["serial"]))
        mqtt_ok = test_printer_mqtt(printer, timeout)

        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        output = snapshot_dir / safe_name / f"{timestamp}-connection-test.jpg"
        LOG.info("[%s] testing camera connection to %s:6000", name, printer["host"])
        try:
            capture_p1s_snapshot(
                str(printer["host"]),
                str(printer["access_code"]),
                output,
                timeout=float(printer.get("camera_timeout_seconds", timeout)),
                warmup_frames=int(printer.get("camera_warmup_frames", 2)),
            )
            LOG.info("[%s] camera snapshot saved locally: %s", name, output)
            camera_ok = True
        except Exception:
            LOG.exception("[%s] camera connection test failed", name)
            camera_ok = False

        printer_ok = mqtt_ok and camera_ok
        LOG.info("[%s] connection test result: %s", name, "OK" if printer_ok else "FAILED")
        all_ok = all_ok and printer_ok

    LOG.info("Bambu connection test completed: %s", "OK" if all_ok else "FAILED")
    return all_ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c", "--config",
        default="/etc/bambu-telegram/config.yaml",
        help="YAML config file",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--test-bambu",
        action="store_true",
        help="test MQTT and camera connections, save a local snapshot, then exit",
    )
    modes.add_argument(
        "--find-telegram-chat-id",
        action="store_true",
        help="wait for a bot message, print discovered Telegram chat IDs, then exit",
    )
    modes.add_argument(
        "--clean-snapshots",
        action="store_true",
        help="delete local JPEG snapshots from the configured snapshot directory, then exit",
    )
    parser.add_argument(
        "--test-timeout",
        type=float,
        default=10.0,
        help="connection-test timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--test-output-dir",
        type=Path,
        default=Path("bambu-test-snapshots"),
        help="local directory for test snapshots (default: ./bambu-test-snapshots)",
    )
    parser.add_argument(
        "--telegram-wait",
        type=int,
        default=30,
        help="seconds to wait for a Telegram message in chat-ID lookup mode (default: 30)",
    )
    parser.add_argument(
        "--telegram-include-old",
        action="store_true",
        help="include pending old updates when looking up Telegram chat IDs",
    )
    args = parser.parse_args()

    if args.test_timeout <= 0:
        parser.error("--test-timeout must be greater than zero")
    if args.telegram_wait < 0:
        parser.error("--telegram-wait must not be negative")

    cfg = load_config(
        Path(args.config),
        require_telegram=not (args.test_bambu or args.clean_snapshots),
        require_chat_id=not args.find_telegram_chat_id,
    )
    configure_logging(cfg)

    if args.clean_snapshots:
        data_dir = Path(cfg.get("data_dir", "/var/lib/bambu-telegram"))
        snapshot_dir = Path(cfg.get("snapshot_dir", data_dir / "snapshots"))
        clean_snapshots(snapshot_dir)
        return 0
    if args.test_bambu:
        return 0 if run_bambu_connection_test(
            cfg, args.test_timeout, args.test_output_dir
        ) else 1
    if args.find_telegram_chat_id:
        return 0 if find_telegram_chat_ids(
            cfg, args.telegram_wait, args.telegram_include_old
        ) else 1

    def handle_signal(signum, frame):
        STOP.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    data_dir = Path(cfg.get("data_dir", "/var/lib/bambu-telegram"))
    snapshot_dir = Path(cfg.get("snapshot_dir", data_dir / "snapshots"))
    state_store = PersistentState(Path(cfg.get("state_file", data_dir / "state.json")))
    telegram = TelegramClient(cfg["telegram"])

    runtimes = [
        PrinterRuntime(pcfg, telegram, state_store, snapshot_dir)
        for pcfg in cfg["printers"]
        if pcfg.get("enabled", True)
    ]

    for runtime in runtimes:
        runtime.run()

    LOG.info("Monitoring %d printer(s)", len(runtimes))
    cleanup_interval = float(cfg.get("snapshot_cleanup_interval_hours", 6)) * 60 * 60
    next_cleanup = 0.0

    try:
        while not STOP.wait(1):
            if cleanup_interval > 0 and time.monotonic() >= next_cleanup:
                try:
                    run_scheduled_snapshot_cleanup(cfg, snapshot_dir)
                except Exception:
                    LOG.exception("Scheduled snapshot cleanup failed")
                next_cleanup = time.monotonic() + cleanup_interval
    finally:
        LOG.info("Stopping")
        for runtime in runtimes:
            runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
