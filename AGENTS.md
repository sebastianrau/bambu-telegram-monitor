# AGENTS.md

## Project

Bambu P1S Telegram Monitor is a Python daemon for monitoring multiple Bambu Lab P1S printers on a trusted LAN and sending event snapshots through the Telegram Bot API.

## Core flow

1. Connect to each configured printer via MQTT/TLS TCP 8883.
2. Deep-merge partial MQTT reports into accumulated printer state.
3. Detect configured milestones/state transitions.
4. Capture a JPEG from the P1S camera using the local TLS protocol on TCP 6000.
5. Send the JPEG using Telegram Bot API `sendPhoto`.
6. Persist sent-event flags to avoid duplicate notifications after restarts.

## Required trigger semantics

Do not change without explicit requirement:

- `layer1`: `layer_num >= 2`
- `started`: newly detected job in `PREPARE` or `RUNNING`; do not emit when the
  first persisted observation is already an active print
- `progress50`: first `mc_percent >= 50`
- `finished`: transition from a previously observed `mc_percent < 100` to
  `mc_percent >= 100`; the same delta may already contain `gcode_state ==
  "FINISH"`, but FINISH alone must not trigger it
- `pause`: transition into `gcode_state == "PAUSE"`
- `failed`: `gcode_state == "FAILED"`
- User-facing FAILED wording: `Druck abgebrochen/fehlgeschlagen`

MQTT messages may be delta updates. Always merge before evaluation.

## Telegram

Configuration:

```yaml
telegram:
  bot_token: "..."
  chat_id: "..."
  caption: "🖨️ {printer}: {milestone} ({progress}%)"
```

Current implementation calls:

```text
POST https://api.telegram.org/bot<TOKEN>/sendPhoto
```

and uploads the JPEG as multipart/form-data.

Do not log the bot token.

## Protocol assumptions

### Bambu MQTT
- TLS TCP 8883
- username `bblp`
- password LAN Access Code
- `device/<serial>/report`
- `device/<serial>/request`
- self-signed printer certificate

### P1S camera
- TLS TCP 6000
- P1/A1 JPEG frame protocol
- 80-byte auth packet
- 16-byte frame header + JPEG payload

Keep reverse-engineered printer protocol code isolated.

## Security

Never commit:
- Bambu LAN Access Codes
- Telegram bot tokens
- real production `config.yaml`

## Recommended engineering improvements

1. Unit-test state/milestone transitions.
2. Move camera + Telegram HTTP work out of MQTT callback into a bounded worker queue.
3. Add deterministic event IDs.
4. Add delivery retry/backoff.
5. Add startup configuration validation.
6. Add CLI tests for MQTT, camera, and Telegram.
7. Support environment variables/systemd credentials for secrets.

## Validation

```bash
python3 -m py_compile bambu_monitor.py
```

If tests exist:

```bash
python3 -m unittest discover -v
```

## Files

- `bambu_monitor.py`
- `config.example.yaml`
- `bambu-telegram.service`
- `install.sh`
- `requirements.txt`
- `INSTALL.md`
- `README.md`
- `CODEX_HANDOFF.md`
