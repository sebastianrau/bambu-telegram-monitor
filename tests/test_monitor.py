import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


# Allow the state-machine tests to run without installing the MQTT client.
try:
    import paho.mqtt.client  # noqa: F401
except ImportError:
    client_module = types.ModuleType("paho.mqtt.client")
    client_module.Client = object
    client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
    mqtt_module = types.ModuleType("paho.mqtt")
    mqtt_module.client = client_module
    paho_module = types.ModuleType("paho")
    paho_module.mqtt = mqtt_module
    sys.modules.update({
        "paho": paho_module,
        "paho.mqtt": mqtt_module,
        "paho.mqtt.client": client_module,
    })

try:
    import requests  # noqa: F401
except ImportError:
    requests_module = types.ModuleType("requests")
    requests_module.post = Mock()
    requests_module.get = Mock()
    sys.modules["requests"] = requests_module

try:
    import yaml  # noqa: F401
except ImportError:
    yaml_module = types.ModuleType("yaml")
    yaml_module.safe_load = Mock()
    sys.modules["yaml"] = yaml_module

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bambu_monitor import (  # noqa: E402
    PersistentState,
    PrinterRuntime,
    TelegramClient,
    clean_snapshots,
    extract_telegram_chats,
    extract_telegram_id_requests,
    find_telegram_chat_ids,
    format_duration,
    load_config,
    mqtt_properties_summary,
    run_bambu_connection_test,
    run_scheduled_snapshot_cleanup,
)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.state = PersistentState(Path(self.tempdir.name) / "state.json")
        self.runtime = PrinterRuntime(
            cfg={
                "name": "Test P1S",
                "host": "192.0.2.1",
                "serial": "SERIAL1",
                "access_code": "secret",
                "notifications": {},
            },
            telegram=Mock(),
            state_store=self.state,
            snapshot_dir=Path(self.tempdir.name),
        )
        self.events = []

        def record(key, label, progress, layer, total_layers):
            self.events.append(key)
            self.state.update_printer("SERIAL1", {f"{key}_sent": True})

        self.runtime.fire = record

    def tearDown(self):
        self.tempdir.cleanup()

    def seed_running(self, progress=1):
        self.state.update_printer("SERIAL1", {
            "task_id": "job-1",
            "last_gcode_state": "RUNNING",
            "last_progress": progress,
        })

    def test_layer_one_completion_emits_once(self):
        self.seed_running()
        self.runtime.evaluate({"task_id": "job-1", "gcode_state": "RUNNING", "mc_percent": 5, "layer_num": 2})
        self.runtime.evaluate({"task_id": "job-1", "gcode_state": "RUNNING", "mc_percent": 6, "layer_num": 3})
        self.assertEqual(self.events, ["layer1"])

    def test_new_job_emits_started_once(self):
        self.state.update_printer("SERIAL1", {
            "task_id": "old-job",
            "last_gcode_state": "IDLE",
            "last_progress": 100,
        })
        started = {
            "task_id": "new-job",
            "gcode_state": "RUNNING",
            "mc_percent": 1,
            "layer_num": 1,
        }
        self.runtime.evaluate(started)
        self.runtime.evaluate(started)
        self.assertEqual(self.events, ["started"])

    def test_first_observation_mid_print_does_not_emit_started(self):
        self.runtime.evaluate({
            "task_id": "active-job",
            "gcode_state": "RUNNING",
            "mc_percent": 20,
            "layer_num": 5,
        })
        self.assertNotIn("started", self.events)

    def test_progress_50_emits_once(self):
        self.seed_running(49)
        self.state.update_printer("SERIAL1", {"layer1_sent": True})
        for progress in (49, 50, 51):
            self.runtime.evaluate({"task_id": "job-1", "gcode_state": "RUNNING", "mc_percent": progress, "layer_num": 20})
        self.assertEqual(self.events, ["progress50"])

    def test_99_percent_running_does_not_finish(self):
        self.seed_running(98)
        self.state.update_printer("SERIAL1", {"layer1_sent": True, "progress50_sent": True})
        self.runtime.evaluate({"task_id": "job-1", "gcode_state": "RUNNING", "mc_percent": 99, "layer_num": 99})
        self.assertNotIn("finished", self.events)

    def test_100_percent_running_emits_once_and_finish_does_not_duplicate(self):
        self.seed_running(99)
        self.state.update_printer("SERIAL1", {"layer1_sent": True, "progress50_sent": True})
        complete = {"task_id": "job-1", "gcode_state": "RUNNING", "mc_percent": 100, "layer_num": 100}
        terminal = {"task_id": "job-1", "gcode_state": "FINISH", "mc_percent": 100, "layer_num": 100}
        self.runtime.evaluate(complete)
        self.runtime.evaluate(terminal)
        self.assertEqual(self.events, ["finished"])

    def test_100_percent_arriving_with_finish_emits_final_snapshot(self):
        self.seed_running(99)
        self.state.update_printer("SERIAL1", {
            "layer1_sent": True,
            "progress50_sent": True,
        })
        self.runtime.evaluate({
            "task_id": "job-1", "gcode_state": "FINISH",
            "mc_percent": 100, "layer_num": 100,
        })
        self.assertEqual(self.events, ["finished"])

    def test_finish_below_100_does_not_emit_final_snapshot(self):
        self.seed_running(99)
        self.state.update_printer("SERIAL1", {"layer1_sent": True, "progress50_sent": True})
        self.runtime.evaluate({
            "task_id": "job-1", "gcode_state": "FINISH",
            "mc_percent": 99, "layer_num": 100,
        })
        self.assertNotIn("finished", self.events)

    def test_stale_100_percent_idle_does_not_emit_final_snapshot(self):
        stale = {
            "task_id": "old-job", "gcode_state": "IDLE",
            "mc_percent": 100, "layer_num": 100,
        }
        self.runtime.evaluate(stale)
        self.runtime.evaluate(stale)
        self.assertNotIn("finished", self.events)

    def test_disconnect_log_contains_diagnostics(self):
        reason = types.SimpleNamespace(value=128, is_failure=True)
        flags = types.SimpleNamespace(is_disconnect_packet_from_server=False)
        properties = types.SimpleNamespace(
            ReasonString="printer closed connection",
            ServerReference=None,
        )
        self.runtime.connected_at = time.monotonic() - 12
        self.runtime.last_message_at = time.monotonic() - 3
        self.runtime.message_count = 42

        with self.assertLogs("bambu-monitor", level="WARNING") as logs:
            self.runtime.on_disconnect(None, None, flags, reason, properties)

        message = logs.output[0]
        self.assertIn("code=128", message)
        self.assertIn("failure=True", message)
        self.assertIn("messages=42", message)
        self.assertIn("printer closed connection", message)

    def test_mqtt_log_format_helpers(self):
        self.assertEqual(format_duration(None), "n/a")
        self.assertEqual(format_duration(1.25), "1.2s")
        self.assertEqual(mqtt_properties_summary(None), "none")

    def test_terminal_state_at_start_is_a_persistent_baseline(self):
        terminal = {"task_id": "old-job", "gcode_state": "FINISH", "mc_percent": 100, "layer_num": 100}
        self.runtime.evaluate(terminal)
        self.runtime.evaluate(terminal)
        self.assertEqual(self.events, [])
        self.assertTrue(self.state.printer("SERIAL1")["finished_sent"])

    def test_repeated_pause_emits_once(self):
        self.seed_running(20)
        self.state.update_printer("SERIAL1", {"layer1_sent": True})
        paused = {"task_id": "job-1", "gcode_state": "PAUSE", "mc_percent": 20, "layer_num": 5}
        self.runtime.evaluate(paused)
        self.runtime.evaluate(paused)
        self.assertEqual(self.events, ["pause"])

    def test_running_to_failed_emits_once(self):
        self.seed_running(20)
        self.state.update_printer("SERIAL1", {"layer1_sent": True})
        failed = {"task_id": "job-1", "gcode_state": "FAILED", "mc_percent": 20, "layer_num": 5}
        self.runtime.evaluate(failed)
        self.runtime.evaluate(failed)
        self.assertEqual(self.events, ["failed"])


class ConfigTests(unittest.TestCase):
    def config_path(self):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "config.yaml"
        path.write_text("test fixture")
        self.addCleanup(directory.cleanup)
        return directory, path

    def test_rejects_missing_telegram_token(self):
        _, path = self.config_path()
        cfg = {
            "printers": [{"host": "192.0.2.1", "serial": "SERIAL1", "access_code": "secret"}],
            "telegram": {"chat_id": "1"},
        }
        with patch("bambu_monitor.yaml.safe_load", return_value=cfg):
            with self.assertRaisesRegex(ValueError, "bot_token"):
                load_config(path)

    def test_rejects_all_disabled_printers(self):
        _, path = self.config_path()
        cfg = {
            "printers": [{"enabled": False}],
            "telegram": {"bot_token": "token", "chat_id": "1"},
        }
        with patch("bambu_monitor.yaml.safe_load", return_value=cfg):
            with self.assertRaisesRegex(ValueError, "enabled"):
                load_config(path)


class PersistenceAndTelegramTests(unittest.TestCase):
    def test_sent_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            PersistentState(path).update_printer("SERIAL1", {"finished_sent": True})
            self.assertTrue(PersistentState(path).printer("SERIAL1")["finished_sent"])

    def test_telegram_sends_multipart_with_caption_and_chat(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True}
        client = TelegramClient({
            "bot_token": "token",
            "chat_id": "123",
            "caption": "{printer}: {milestone} ({progress}%)",
        })
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "snapshot.jpg"
            image.write_bytes(b"jpeg")
            with patch("bambu_monitor.requests.post", return_value=response) as post:
                client.send_image(image, "P1S", "Druck fertig", 100)

        call = post.call_args
        self.assertEqual(call.kwargs["data"]["chat_id"], "123")
        self.assertEqual(call.kwargs["data"]["caption"], "P1S: Druck fertig (100%)")
        self.assertEqual(call.kwargs["files"]["photo"][0], "snapshot.jpg")

    def test_extracts_private_and_group_chat_ids(self):
        chats = extract_telegram_chats([
            {"message": {"chat": {"id": 123, "type": "private", "first_name": "Basti"}}},
            {"message": {"chat": {"id": -456, "type": "group", "title": "Printers"}}},
        ])
        self.assertEqual(chats["123"]["name"], "Basti")
        self.assertEqual(chats["-456"]["type"], "group")

    def test_extracts_id_command_with_bot_suffix(self):
        requests_by_chat = extract_telegram_id_requests([
            {"message": {
                "text": "/id@MyPrinterBot",
                "chat": {"id": -456, "type": "group"},
            }},
        ])
        self.assertIn("-456", requests_by_chat)

    def test_chat_id_lookup_uses_long_polling(self):
        pending = Mock()
        pending.json.return_value = {
            "ok": True,
            "result": [{"update_id": 40, "message": {"chat": {"id": 999}}}],
        }
        fresh = Mock()
        fresh.json.return_value = {
            "ok": True,
            "result": [{"update_id": 41, "message": {
                "text": "/id",
                "chat": {"id": 123, "type": "private"},
            }}],
        }
        sent = Mock()
        sent.json.return_value = {"ok": True}
        cfg = {"telegram": {"bot_token": "token"}}
        with patch("bambu_monitor.requests.get", side_effect=[pending, fresh]) as get:
            with patch("bambu_monitor.requests.post", return_value=sent) as post:
                self.assertTrue(find_telegram_chat_ids(cfg, 30))
        self.assertEqual(get.call_args_list[0].kwargs["params"]["offset"], -1)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 41)
        self.assertEqual(post.call_args.kwargs["data"]["chat_id"], "123")
        self.assertIn("123", post.call_args.kwargs["data"]["text"])


class ConnectionTestTests(unittest.TestCase):
    def test_connection_test_checks_mqtt_and_saves_camera_image(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = {
                "snapshot_dir": directory,
                "printers": [{
                    "name": "Test P1S",
                    "host": "192.0.2.1",
                    "serial": "SERIAL1",
                    "access_code": "secret",
                }],
            }

            def fake_capture(host, access_code, output, timeout, warmup_frames):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"jpeg")

            with patch("bambu_monitor.test_printer_mqtt", return_value=True) as mqtt_test:
                with patch("bambu_monitor.capture_p1s_snapshot", side_effect=fake_capture):
                    output_dir = Path(directory) / "local-output"
                    self.assertTrue(run_bambu_connection_test(cfg, 3, output_dir))

            mqtt_test.assert_called_once_with(cfg["printers"][0], 3)
            images = list(output_dir.glob("Test_P1S/*.jpg"))
            self.assertEqual(len(images), 1)


class SnapshotCleanupTests(unittest.TestCase):
    def test_deletes_only_snapshot_images_and_preserves_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "snapshots"
            printer = root / "P1S"
            printer.mkdir(parents=True)
            (printer / "one.jpg").write_bytes(b"jpeg")
            (printer / "two.JPEG").write_bytes(b"jpeg")
            keep = printer / "keep.txt"
            keep.write_text("keep")

            self.assertEqual(clean_snapshots(root), 2)
            self.assertTrue(root.is_dir())
            self.assertTrue(keep.exists())
            self.assertFalse((printer / "one.jpg").exists())

    def test_refuses_home_directory(self):
        with self.assertRaisesRegex(ValueError, "unsafe"):
            clean_snapshots(Path.home())

    def test_scheduled_cleanup_preserves_recent_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "snapshots"
            root.mkdir()
            old = root / "old.jpg"
            recent = root / "recent.jpg"
            old.write_bytes(b"old")
            recent.write_bytes(b"recent")
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(old, (old_time, old_time))

            removed = run_scheduled_snapshot_cleanup(
                {"snapshot_retention_days": 1}, root
            )

            self.assertEqual(removed, 1)
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())

if __name__ == "__main__":
    unittest.main()
