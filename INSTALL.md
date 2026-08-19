# Bambu P1S Telegram Monitor

## Installation und Konfiguration

Stand: August 2026

Der **Bambu P1S Telegram Monitor** überwacht einen oder mehrere Bambu Lab P1S im lokalen Netzwerk. Der Druckstatus wird lokal per MQTT/TLS gelesen. Bei konfigurierten Ereignissen wird ein aktuelles Bild der P1S-Kamera aufgenommen und über einen Telegram-Bot verschickt.

## Funktionen

- mehrere P1S gleichzeitig
- Bild beim Start eines neuen Druckauftrags
- Bild nach abgeschlossenem Layer 1
- Bild bei 50 % Druckfortschritt
- Bild bei tatsächlichem Druckende (`FINISH`)
- Bild bei Pause (`PAUSE`)
- Bild bei Abbruch/Fehler (`FAILED`)
- persistenter Zustand gegen doppelte Meldungen
- systemd-Service
- kein WhatsApp Business Account erforderlich

## Architektur

```text
P1S #1 -- MQTT/TLS :8883 --\
P1S #2 -- MQTT/TLS :8883 ---+--> bambu-telegram.service
P1S #n -- MQTT/TLS :8883 --/           |
                                        +-- Kamera TLS :6000 --> JPEG
                                        +-- state.json
                                        +-- Snapshots
                                        \-- HTTPS --> Telegram Bot API
```

## Voraussetzungen

- Raspberry Pi OS, Debian oder Ubuntu
- Python 3 und `python3-venv`
- Netzwerkzugriff auf die P1S
- LAN Access Code und Seriennummer jedes P1S
- Telegram-Account
- eigener Telegram-Bot
- empfohlen: DHCP-Reservierung für jeden Drucker

## Netzwerk

| Richtung | Ziel | Port | Zweck |
|---|---|---:|---|
| Service -> P1S | Drucker | TCP 8883 | MQTT/TLS |
| Service -> P1S | Drucker | TCP 6000 | Kamera/JPEG |
| Service -> Internet | Telegram API | TCP 443 | Bildversand |

## Telegram-Bot einrichten

### 1. Bot erstellen

In Telegram den offiziellen **@BotFather** öffnen.

```text
/newbot
```

Bot-Namen und Benutzernamen vergeben. Danach liefert BotFather einen Token, z. B.:

```text
1234567890:AAExampleBotToken
```

Der Token ist ein Secret und gehört nicht in Git.

### 2. Bot anschreiben

Den neuen Bot in Telegram öffnen und mindestens eine Nachricht senden, z. B.:

```text
/start
```

Ein Bot kann einen privaten Benutzer nicht von sich aus erstmals anschreiben. Der Chat muss vorher durch den Benutzer begonnen werden.

### 3. Chat-ID später mit dem Monitor ermitteln

Die numerische Chat-ID wird nicht gut sichtbar in der Telegram-App angezeigt.
Nach der Installation kann der Monitor sie selbst ermitteln: Im speziellen
Suchmodus antwortet der Bot auf `/id` direkt mit der ID. Die vollständige
Anleitung folgt im Abschnitt [Telegram-Chat-ID ermitteln](#telegram-chat-id-ermitteln).

> Den Bot-Token nicht in Screenshots, Logs oder öffentliche Dokumente kopieren.

## Installation

### 1. Pakete

```bash
sudo apt update
sudo apt install -y python3 python3-venv unzip
```

### 2. Projekt

```bash
unzip bambu-telegram-monitor-codex.zip
cd bambu-telegram-monitor
```

### 3. Installieren

```bash
sudo ./install.sh
```

Es werden angelegt:

```text
/opt/bambu-telegram/
/etc/bambu-telegram/config.yaml
/var/lib/bambu-telegram/
/etc/systemd/system/bambu-telegram.service
```

### 4. Konfiguration

```bash
sudo nano /etc/bambu-telegram/config.yaml
```

## Beispielkonfiguration

```yaml
log_level: INFO
data_dir: /var/lib/bambu-telegram
snapshot_dir: /var/lib/bambu-telegram/snapshots
state_file: /var/lib/bambu-telegram/state.json

printers:
  - name: P1S Büro
    enabled: true
    host: 192.168.192.50
    serial: "01P00A123456789"
    access_code: "12345678"
    camera_timeout_seconds: 10
    camera_warmup_frames: 2
    event_queue_size: 16
    delivery_attempts: 3
    delivery_retry_seconds: 5

    notifications:
      started: true
      layer1: true
      progress50: true
      finished: true
      pause: true
      failed: true

telegram:
  bot_token: "1234567890:AAExampleBotToken"
  chat_id: "123456789"
  caption: "🖨️ {printer}: {milestone} ({progress}%)"
  disable_notification: false
  protect_content: false
  timeout_seconds: 30
```

## Telegram-Chat-ID ermitteln

### Privaten Chat ermitteln

1. In `/etc/bambu-telegram/config.yaml` den von BotFather erhaltenen
   `bot_token` eintragen. Für die Suche darf `chat_id` zunächst fehlen oder leer
   sein.
2. Den Suchmodus starten:

```bash
sudo -u bambu-monitor /opt/bambu-telegram/venv/bin/python \
  /opt/bambu-telegram/bambu_monitor.py \
  --config /etc/bambu-telegram/config.yaml \
  --find-telegram-chat-id \
  --telegram-wait 60
```

3. Erst nachdem `Waiting ... Send /id` erscheint, in Telegram `/id` an den Bot
   senden.
4. Der Bot antwortet im selben Chat, beispielsweise:

```text
Your Telegram chat ID is: 123456789
```

5. Diese Zahl als `chat_id` in die Konfiguration übernehmen:

```yaml
telegram:
  bot_token: "BOT_TOKEN"
  chat_id: "123456789"
```

Die ID erscheint zusätzlich lokal als `Telegram chat found: id=...` im Log.
Alte Telegram-Updates werden standardmäßig verworfen, damit nur das neu
gesendete `/id` verwendet wird.

### Gruppen-ID ermitteln

1. Den Bot zur gewünschten Telegram-Gruppe hinzufügen.
2. Den Suchmodus wie oben starten.
3. `/id` in der Gruppe senden. Je nach Gruppeneinstellung kann Telegram den
   Befehl als `/id@NameDesBots` übertragen; beide Formen werden unterstützt.
4. Der Bot antwortet in der Gruppe mit deren ID. Gruppen-IDs sind üblicherweise
   negativ, zum Beispiel `-1001234567890`. Das Minuszeichen gehört zur ID und
   muss in `config.yaml` übernommen werden.

### Fehlerbehebung

- `No /id command found`: Suchmodus erneut starten und `/id` erst senden,
  nachdem die Wartemeldung erscheint.
- Aktiver Webhook: Telegram erlaubt `getUpdates` nicht gleichzeitig mit einem
  Webhook. Der Monitor meldet die erkannte Webhook-Adresse im Log.
- Alte Nachrichten absichtlich durchsuchen:

```bash
sudo -u bambu-monitor /opt/bambu-telegram/venv/bin/python \
  /opt/bambu-telegram/bambu_monitor.py \
  --config /etc/bambu-telegram/config.yaml \
  --find-telegram-chat-id \
  --telegram-include-old
```

## Telegram-Konfiguration

| Parameter | Bedeutung |
|---|---|
| `bot_token` | von BotFather ausgegebener Bot-Token |
| `chat_id` | Zielchat, Gruppe oder Kanal |
| `caption` | Text unter dem Foto |
| `disable_notification` | `true` sendet lautlos |
| `protect_content` | schützt Weiterleitung/Speichern, soweit Telegram dies unterstützt |
| `timeout_seconds` | HTTP-Timeout |

Verfügbare Platzhalter in `caption`:

```text
{printer}
{milestone}
{progress}
```

Beispiel:

```text
🖨️ P1S Büro: Druck fertig (100%)
```

## P1S-Konfiguration

### IP-Adresse

IP am Drucker oder Router ermitteln. DHCP-Reservierung empfohlen.

### LAN Access Code

Im Netzwerk-/WLAN-Bereich des P1S. Der lokale Benutzername ist:

```text
bblp
```

### Seriennummer

Für die MQTT-Topics:

```text
device/<SERIAL>/report
device/<SERIAL>/request
```

## Ereignisse

| Option | Trigger | Meldung |
|---|---|---|
| `started` | neuer Druckauftrag in `PREPARE` oder `RUNNING` | Druck gestartet |
| `layer1` | `layer_num >= 2` | Layer 1 fertig |
| `progress50` | `mc_percent >= 50` | 50 % erreicht |
| `finished` | `gcode_state == FINISH` | Druck fertig |
| `pause` | `gcode_state == PAUSE` | Druck pausiert |
| `failed` | `gcode_state == FAILED` | Druck abgebrochen/fehlgeschlagen |

## Lokaler Verbindungstest

Vor dem Start des Dienstes können MQTT und Kamera aller aktivierten Drucker
getestet werden:

```bash
sudo -u bambu-monitor /opt/bambu-telegram/venv/bin/python \
  /opt/bambu-telegram/bambu_monitor.py \
  --config /etc/bambu-telegram/config.yaml \
  --test-bambu
```

Der Test wartet auf einen MQTT-Statusbericht, nimmt anschließend ein Kamerabild
auf und speichert es unter
`./bambu-test-snapshots/`. Alle Schritte und Fehler werden ins Log geschrieben;
Telegram wird nicht verwendet. Mit `--test-timeout 20` lässt sich der MQTT-Test-
Timeout ändern. Ein anderes Speicherziel kann mit
`--test-output-dir <Verzeichnis>` angegeben werden.

## Service

Start:

```bash
sudo systemctl start bambu-telegram
```

Status:

```bash
sudo systemctl status bambu-telegram
```

Autostart:

```bash
sudo systemctl enable bambu-telegram
```

Nach Konfigurationsänderungen:

```bash
sudo systemctl restart bambu-telegram
```

Logs:

```bash
sudo journalctl -u bambu-telegram -f
```

## Telegram separat testen

Mit einem vorhandenen JPEG:

```bash
curl -X POST \
  "https://api.telegram.org/bot<BOT_TOKEN>/sendPhoto" \
  -F "chat_id=<CHAT_ID>" \
  -F "caption=Test vom Bambu Monitor" \
  -F "photo=@test.jpg"
```

Damit lässt sich der Telegram-Teil unabhängig vom Drucker testen.

## Snapshots

```text
/var/lib/bambu-telegram/snapshots/
```

## Persistenter Zustand

```text
/var/lib/bambu-telegram/state.json
```

Nicht im normalen Betrieb löschen, da die Datei bereits versendete Ereignisse speichert.

## Fehlerbehebung

### Telegram liefert nichts

Prüfen:

- Bot bereits per `/start` angeschrieben?
- `bot_token` korrekt?
- `chat_id` korrekt?
- Internetzugriff über HTTPS?
- Bei Gruppen: Bot tatsächlich Mitglied der Gruppe?

### MQTT verbindet nicht

Prüfen:

- P1S IP/Hostname
- Seriennummer
- LAN Access Code
- TCP 8883

### Kein Kamerabild

Prüfen:

- LAN Access Code
- TCP 6000
- Kamera erreichbar

## Sicherheit

- Telegram Bot Token und Bambu Access Codes wie Passwörter behandeln.
- `config.yaml` niemals committen.
- `/etc/bambu-telegram/config.yaml` wird durch die Installation auf restriktive Dateirechte gesetzt.
- Lokale Bambu-Zertifikate sind selbstsigniert; für diese lokalen Verbindungen ist die Zertifikatsprüfung im Dienst deaktiviert.

## Kurzreferenz

```bash
sudo nano /etc/bambu-telegram/config.yaml
sudo systemctl restart bambu-telegram
sudo systemctl status bambu-telegram
sudo journalctl -u bambu-telegram -f
ls -lah /var/lib/bambu-telegram/snapshots/
cat /var/lib/bambu-telegram/state.json
```
