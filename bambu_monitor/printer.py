from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt

from .common import LOG, STOP, as_int, format_duration, mqtt_properties_summary
from .messaging.base import MessageClient
from .printers import create_printer
from .state import PersistentState

@dataclass
class PrinterRuntime:
    cfg: dict
    messenger: MessageClient
    state_store: PersistentState
    snapshot_dir: Path
    mqtt_state: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    client: Optional[mqtt.Client] = None
    event_queue: queue.Queue = field(init=False)
    worker: Optional[threading.Thread] = field(default=None, init=False)
    connected_at: Optional[float] = field(default=None, init=False)
    last_message_at: Optional[float] = field(default=None, init=False)
    message_count: int = field(default=0, init=False)

    def __post_init__(self):
        self.printer = create_printer(self.cfg)
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
        self.client = self.printer.create_mqtt_client(client_id)

        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

        LOG.info(
            "[%s] connecting MQTT %s:%d",
            self.name, self.host, self.printer.mqtt_port,
        )
        self.printer.connect_mqtt(self.client)
        self.client.loop_start()

    def stop(self):
        if self.client:
            self.client.disconnect()
            self.client.loop_stop()
        if self.worker:
            try:
                self.event_queue.put_nowait(None)
            except queue.Full:
                LOG.debug("[%s] event queue full during shutdown", self.name)
            # Snapshot capture and Telegram uploads use blocking I/O. The
            # worker is a daemon, so they must not delay Ctrl-C shutdown.
            self.worker.join(timeout=1)
            if self.worker.is_alive():
                LOG.debug("[%s] event worker still active; leaving daemon thread", self.name)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            LOG.error(
                "[%s] MQTT connection failed: reason=%s code=%s host=%s:8883 properties=%s",
                self.name,
                reason_code,
                getattr(reason_code, "value", reason_code),
                self.host,
                mqtt_properties_summary(properties),
            )
            return

        self.connected_at = time.monotonic()
        self.last_message_at = None
        self.message_count = 0
        LOG.info("[%s] MQTT connected", self.name)
        self.printer.on_mqtt_connected(client)

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        if not STOP.is_set():
            now = time.monotonic()
            connected_seconds = (
                now - self.connected_at if self.connected_at is not None else None
            )
            last_message_seconds = (
                now - self.last_message_at if self.last_message_at is not None else None
            )
            LOG.warning(
                "[%s] MQTT disconnected: reason=%s code=%s failure=%s "
                "server_packet=%s host=%s:8883 connected_for=%s "
                "last_message_ago=%s messages=%d reconnect=automatic properties=%s",
                self.name,
                reason_code,
                getattr(reason_code, "value", reason_code),
                getattr(reason_code, "is_failure", "unknown"),
                getattr(
                    disconnect_flags,
                    "is_disconnect_packet_from_server",
                    "unknown",
                ),
                self.host,
                format_duration(connected_seconds),
                format_duration(last_message_seconds),
                self.message_count,
                mqtt_properties_summary(properties),
            )
        self.connected_at = None

    def on_message(self, client, userdata, msg):
        self.last_message_at = time.monotonic()
        self.message_count += 1
        with self.lock:
            try:
                print_state = self.printer.decode_mqtt_message(
                    msg.payload, self.mqtt_state
                )
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
        previous_progress = as_int(persisted.get("last_progress"))
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

        # Trigger shortly before completion on the actual progress crossing.
        # Requiring a previously observed value below 99 avoids
        # stale notifications when starting against an old completed job.
        persisted = self.state_store.printer(self.serial)
        if (
            notifications.get("finished", True)
            and progress is not None
            and progress >= 99
            and previous_progress is not None
            and previous_progress < 99
            and not persisted.get("finished_sent", False)
        ):
            self.fire("finished", "99 % erreicht", progress, layer, total_layers)
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

        event = (milestone_key, milestone_label, progress, layer, total_layers, flag)
        try:
            self.event_queue.put_nowait(event)
        except queue.Full:
            self.state_store.update_printer(self.serial, {flag: False})
            LOG.error("[%s] event queue full, dropping %s", self.name, milestone_label)

    def request_manual_snapshot(self) -> bool:
        with self.lock:
            print_state = self.mqtt_state.get("print", {})
            progress = as_int(print_state.get("mc_percent")) or 0
            layer = as_int(print_state.get("layer_num"))
            total_layers = as_int(print_state.get("total_layer_num"))
        event = (
            "manual",
            "Manueller Snapshot",
            progress,
            layer,
            total_layers,
            None,
        )
        try:
            self.event_queue.put_nowait(event)
            LOG.info("[%s] manual snapshot queued", self.name)
            return True
        except queue.Full:
            LOG.error("[%s] event queue full, manual snapshot rejected", self.name)
            return False

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
                       layer: Optional[int], total_layers: Optional[int],
                       flag: Optional[str]):

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
                self.printer.capture_snapshot(output)
                LOG.info("[%s] snapshot saved: %s", self.name, output)
                self.messenger.send_image(output, self.name, milestone_label, progress)
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
        if flag is not None:
            self.state_store.update_printer(self.serial, {flag: False})
