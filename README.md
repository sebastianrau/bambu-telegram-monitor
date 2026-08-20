# Bambu P1S Telegram Monitor

Linux service that monitors one or more Bambu Lab P1S printers and sends a current chamber-camera snapshot to Telegram at important print events.

## Notifications

- Print started
- Layer 1 finished
- 50 % progress
- 100 % print progress (captured before the later `FINISH` state)
- Print paused (`PAUSE`)
- Print failed/cancelled (`FAILED`)

## Runtime

- Bambu MQTT/TLS: TCP 8883
- P1S camera TLS/JPEG: TCP 6000
- Telegram Bot API: HTTPS
- Configuration: YAML
- Service manager: systemd

## Documentation

- [Installation and configuration](INSTALL.md)
- [Manual Docker installation on Raspberry Pi](MANUAL_DOCKER_INSTALL.md)
- [Codex handoff](CODEX_HANDOFF.md)
- [Agent instructions](AGENTS.md)

## Quick start

```bash
sudo apt update
sudo apt install -y python3 python3-venv unzip
sudo ./install.sh
sudo nano /etc/bambu-telegram/config.yaml
sudo systemctl start bambu-telegram
sudo journalctl -u bambu-telegram -f
```

## Docker

For installation with the included `Dockerfile`, follow the complete
[manual Docker installation guide](MANUAL_DOCKER_INSTALL.md). It covers image
building, configuration permissions, the persistent data volume, Telegram
chat-ID lookup, container startup, logs, updates, and maintenance.

The running service also removes expired snapshots automatically. Defaults:

```yaml
snapshot_retention_days: 7
snapshot_cleanup_interval_hours: 6
```

Cleanup runs shortly after service startup and then at the configured interval.
Set `snapshot_retention_days: 0` to disable automatic cleanup.

The image runs as an unprivileged user. Configuration is mounted read-only;
snapshots and persistent event state are stored in the Docker volume documented
in the manual guide.

## Lokaler Verbindungstest

MQTT und Kamera aller aktivierten Drucker testen, einen Screenshot lokal speichern
und anschließend beenden:

```bash
/opt/bambu-telegram/venv/bin/python /opt/bambu-telegram/bambu_monitor.py \
  --config /etc/bambu-telegram/config.yaml \
  --test-bambu
```

Die Screenshots werden standardmäßig unter `./bambu-test-snapshots/` gespeichert.
Mit `--test-output-dir <Verzeichnis>` kann das Ziel geändert werden. Der Test sendet
nichts an Telegram und liefert Exit-Code `0` bei Erfolg beziehungsweise `1` bei
mindestens einem fehlgeschlagenen Druckertest.

Damit kein gepufferter alter Kameraframe gespeichert wird, verwirft der Monitor
standardmäßig zwei gültige Frames. Dies lässt sich pro Drucker mit
`camera_warmup_frames` konfigurieren.

## Lokaler Dauerbetrieb auf macOS/Linux

Beim Start ohne systemd müssen beschreibbare lokale Datenpfade in der
Konfiguration verwendet werden:

```yaml
data_dir: ./data
snapshot_dir: ./data/snapshots
state_file: ./data/state.json
```

Die Produktionspfade unter `/var/lib/bambu-telegram` sind für die Installation
als Linux-Systemdienst vorgesehen.

## Telegram-Chat-ID ermitteln

In `config.yaml` wird dafür zunächst nur `telegram.bot_token` benötigt. Danach:

```bash
.venv/bin/python bambu_monitor.py -c ./config.yaml --find-telegram-chat-id
```

Während das Programm wartet, `/id` an den Bot senden. Der Bot antwortet im
gleichen privaten Chat oder in der gleichen Gruppe mit der numerischen Chat-ID;
die ID wird zusätzlich im lokalen Log ausgegeben.
Ältere wartende Updates werden vorher verworfen. Mit `--telegram-wait 60` kann
die Wartezeit verlängert werden; `--telegram-include-old` berücksichtigt auch
bereits vorhandene Updates.

## Telegram camera command

While the monitor is running, send either command from the configured Telegram
chat:

```text
/snapshop
/snapshot
```

The monitor captures a fresh camera image and sends it back to Telegram. With
multiple enabled printers, the command sends one image per printer. Select a
single printer using any case-insensitive part of its configured name, or the
beginning of its serial number:

```text
/snapshot P1S Büro
/snapshot Büro
/snapshot 01P00A
```

Exact matches take priority. If a partial selector matches multiple printers,
the bot lists the matches and asks for a more specific value.

Only `telegram.chat_id` is authorized. Commands from other chats are ignored.
The default cooldown is 10 seconds. Telegram webhooks cannot be used at the same
time because the monitor receives commands through `getUpdates`.

## Security

Never commit real Bambu LAN Access Codes or Telegram bot tokens.
