# Bambu P1S Telegram Monitor

Linux service that monitors one or more Bambu Lab P1S printers and sends a current chamber-camera snapshot to Telegram at important print events.

## Notifications

- Print started
- Layer 1 finished
- 50 % progress
- Print finished (`FINISH`)
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

## Security

Never commit real Bambu LAN Access Codes or Telegram bot tokens.
