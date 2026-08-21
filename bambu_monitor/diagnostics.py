import os
import threading
import time
from pathlib import Path

from .common import LOG
from .printers import create_printer

def test_printer_mqtt(printer: dict, timeout: float) -> bool:
    name = printer.get("name", printer["serial"])
    serial = str(printer["serial"])
    connected = threading.Event()
    report_received = threading.Event()
    connection_error = []

    adapter = create_printer(printer)
    client = adapter.create_mqtt_client(
        f"bambu-test-{serial[-8:]}-{os.getpid()}"
    )

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            connection_error.append(str(reason_code))
            connected.set()
            return
        LOG.info("[%s] MQTT connected", name)
        adapter.on_mqtt_connected(client)
        connected.set()

    def on_message(client, userdata, msg):
        try:
            print_state = adapter.decode_mqtt_message(msg.payload, {})
            if isinstance(print_state, dict):
                report_received.set()
        except Exception:
            LOG.warning("[%s] MQTT test received invalid JSON", name)

    client.on_connect = on_connect
    client.on_message = on_message

    LOG.info("[%s] testing MQTT connection to %s:8883", name, printer["host"])
    try:
        adapter.connect_mqtt_for_test(client)
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
        adapter = create_printer(printer)
        LOG.info(
            "[%s] testing %s camera snapshot from %s",
            name, adapter.model, adapter.host,
        )
        try:
            adapter.cfg.setdefault("camera_timeout_seconds", timeout)
            adapter.capture_snapshot(output)
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
