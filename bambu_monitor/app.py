import argparse
import signal
import time
from pathlib import Path

from .common import LOG, STOP
from .config import configure_logging, load_config
from .diagnostics import run_bambu_connection_test
from .messaging import create_command_poller, create_message_client
from .printer import PrinterRuntime
from .snapshots import clean_snapshots, run_scheduled_snapshot_cleanup
from .state import PersistentState
from .messaging.telegram import find_telegram_chat_ids

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
    messenger = create_message_client(cfg)

    runtimes = [
        PrinterRuntime(pcfg, messenger, state_store, snapshot_dir)
        for pcfg in cfg["printers"]
        if pcfg.get("enabled", True)
    ]

    for runtime in runtimes:
        runtime.run()

    command_poller = create_command_poller(cfg, messenger, runtimes)
    if command_poller:
        command_poller.start()

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
        if command_poller:
            command_poller.stop()
        for runtime in runtimes:
            runtime.stop()
    return 0


