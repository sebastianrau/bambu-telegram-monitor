# Codex Handoff

## Goal

Continue development of the Bambu P1S Telegram Monitor without needing the originating chat.

## Current user-approved scope

Notifications with a current P1S camera image for:

1. Layer 1 complete
2. 50 % progress
3. Print reaches 100 % progress
4. Pause
5. Failure/cancellation

The notification transport has been changed from WhatsApp Cloud API to **Telegram Bot API**. WhatsApp support is no longer part of the current project.

## Decisions already made

- The final snapshot is triggered by the first `mc_percent >= 100`; do not wait
  for the later `gcode_state = FINISH` state.
- Layer 1 complete means `layer_num >= 2`.
- `FAILED` is presented neutrally as `Druck abgebrochen/fehlgeschlagen`.
- P1 MQTT reports are partial/delta; deep-merge before evaluation.
- P1S camera uses local TLS/JPEG on TCP 6000.
- Telegram photos are sent directly with Bot API `sendPhoto`.
- Sent flags persist across service restarts.

## Deployment names

```text
Service:      bambu-telegram.service
Application:  /opt/bambu-telegram
Config:       /etc/bambu-telegram/config.yaml
Data:         /var/lib/bambu-telegram
```

## Telegram config

```yaml
telegram:
  bot_token: "..."
  chat_id: "..."
  caption: "🖨️ {printer}: {milestone} ({progress}%)"
  disable_notification: false
  protect_content: false
  timeout_seconds: 30
```

## Before changing code

Read:

1. `AGENTS.md`
2. `README.md`
3. `INSTALL.md`
4. `config.example.yaml`
5. `bambu_monitor.py`

Run:

```bash
python3 -m py_compile bambu_monitor.py
```

## Best next refactor

Do not alter trigger behavior. Refactor I/O so the MQTT callback only merges state and queues events. Camera and Telegram HTTP operations should run in a bounded worker queue with retry/backoff.

## Tests to add

- layer 1 -> layer 2 emits once
- 49 -> 50 -> 51 emits 50% once
- 99% while RUNNING does not emit the final snapshot
- 99 -> 100% while RUNNING emits the final snapshot once
- a later FINISH does not emit a duplicate
- repeated PAUSE delta reports do not duplicate
- RUNNING -> FAILED emits failed once
- persisted sent state survives restart
- Telegram client sends multipart photo with correct chat ID and caption
