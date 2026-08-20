# Bambu P1S Telegram Monitor

Docker-basierter Monitor für einen oder mehrere Bambu-Lab-P1S-Drucker. Bei
wichtigen Druckereignissen nimmt er ein aktuelles Bild der Innenraumkamera auf
und sendet es über Telegram.

## Benachrichtigungen

- Druck gestartet
- Layer 1 abgeschlossen
- 50 % Druckfortschritt
- 99 % Druckfortschritt, noch vor Druckende
- Druck pausiert (`PAUSE`)
- Druck fehlgeschlagen oder abgebrochen (`FAILED`)

## Laufzeit

- Bambu MQTT/TLS: TCP 8883
- P1S-Kamera mit TLS/JPEG: TCP 6000
- Telegram Bot API: HTTPS
- Konfiguration: YAML
- Bereitstellung: Docker

## Dokumentation

- [Installation und Konfiguration mit Docker](docs/MANUAL_DOCKER_INSTALL.md)
- [Codex-Übergabe](docs/CODEX_HANDOFF.md)
- [Agentenanweisungen](docs/AGENTS.md)

## Installation

Die vollständige
[Anleitung zur manuellen Docker-Installation](docs/MANUAL_DOCKER_INSTALL.md)
beschreibt den Image-Build, die Konfigurationsrechte, das persistente
Daten-Volume, die Ermittlung der Telegram-Chat-ID, den Containerstart, Logs,
Aktualisierungen und Wartung.

Der laufende Container entfernt abgelaufene Snapshots automatisch. Die
Standardwerte lauten:

```yaml
snapshot_retention_days: 7
snapshot_cleanup_interval_hours: 6
```

Die Bereinigung startet kurz nach dem Containerstart und wird anschließend im
konfigurierten Intervall wiederholt. Mit `snapshot_retention_days: 0` wird die
automatische Bereinigung deaktiviert.

Das Image läuft als unprivilegierter Benutzer. Die Konfiguration wird nur lesbar
eingebunden. Snapshots und der persistente Ereignisstatus liegen im
dokumentierten Docker-Volume. Der `docker run`-Befehl bindet außerdem
`/etc/localtime` und `/etc/timezone` nur lesbar ein, damit die Zeitstempel im
Container der Zeitzone des Hosts entsprechen.

## Verbindungstest im Container

Mit diesem Befehl werden MQTT und Kamera aller aktivierten Drucker getestet,
ein Screenshot im Daten-Volume gespeichert und der Container danach beendet:

```bash
sudo docker run --rm \
  --mount type=bind,src=/etc/bambu-telegram/config.yaml,dst=/etc/bambu-telegram/config.yaml,readonly \
  --mount type=volume,src=bambu-telegram-data,dst=/var/lib/bambu-telegram \
  bambu-telegram-monitor:local \
  --config /etc/bambu-telegram/config.yaml \
  --test-bambu \
  --test-output-dir /var/lib/bambu-telegram/connection-tests
```

Der Test sendet nichts an Telegram und liefert Exit-Code `0` bei Erfolg oder
`1`, wenn mindestens ein Druckertest fehlschlägt.

Damit kein gepufferter alter Kameraframe gespeichert wird, verwirft der Monitor
standardmäßig zwei gültige Frames. Dies lässt sich pro Drucker mit
`camera_warmup_frames` konfigurieren.

## Telegram-Chat-ID ermitteln

Zunächst wird in der Konfiguration nur `telegram.bot_token` benötigt. Danach:

```bash
sudo docker run --rm \
  --mount type=bind,src=/etc/bambu-telegram/config.yaml,dst=/etc/bambu-telegram/config.yaml,readonly \
  bambu-telegram-monitor:local \
  --config /etc/bambu-telegram/config.yaml \
  --find-telegram-chat-id
```

Während das Programm wartet, `/id` an den Bot senden. Der Bot antwortet im
gleichen privaten Chat oder in der gleichen Gruppe mit der numerischen Chat-ID.
Die ID wird zusätzlich im lokalen Log ausgegeben. Alte wartende Updates werden
vorher verworfen. Mit `--telegram-wait 60` lässt sich die Wartezeit verlängern;
`--telegram-include-old` berücksichtigt auch bereits vorhandene Updates.

## Kamerabefehl über Telegram

Während der Monitor läuft, kann im konfigurierten Telegram-Chat einer der
folgenden Befehle gesendet werden:

```text
/snapshop
/snapshot
```

Der Monitor nimmt ein aktuelles Kamerabild auf und sendet es an Telegram. Bei
mehreren aktivierten Druckern wird standardmäßig von jedem Drucker ein Bild
gesendet. Ein einzelner Drucker kann über einen beliebigen Teil seines Namens
oder den Anfang seiner Seriennummer ausgewählt werden:

```text
/snapshot P1S Büro
/snapshot Büro
/snapshot 01P00A
```

Exakte Treffer haben Vorrang. Falls eine Teilangabe zu mehreren Druckern passt,
listet der Bot die Treffer auf und fordert eine eindeutigere Auswahl an.

Nur `telegram.chat_id` ist berechtigt. Befehle aus anderen Chats werden
ignoriert. Die Standardsperrzeit beträgt zehn Sekunden. Telegram-Webhooks
können nicht gleichzeitig verwendet werden, weil der Monitor Befehle über
`getUpdates` empfängt.

## Sicherheit

Echte Bambu-LAN-Access-Codes und Telegram-Bot-Token dürfen niemals committet
werden.
