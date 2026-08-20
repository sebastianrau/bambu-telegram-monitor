# Codex-Übergabe

## Ziel

Die Entwicklung des Bambu P1S Telegram Monitors ohne Zugriff auf den
ursprünglichen Chat fortsetzen.

## Aktuell freigegebener Funktionsumfang

Benachrichtigungen mit einem aktuellen Bild der P1S-Kamera bei:

1. Start des Druckauftrags
2. Abschluss von Layer 1
3. 50 % Druckfortschritt
4. Erreichen von 99 % Druckfortschritt
5. Pause
6. Fehler oder Abbruch

Die Benachrichtigungen werden über die **Telegram Bot API** gesendet. WhatsApp
gehört nicht mehr zum Funktionsumfang des Projekts.

## Bereits getroffene Entscheidungen

- Der letzte Snapshot wird beim Übergang von einem zuvor beobachteten
  `mc_percent < 99` zu `mc_percent >= 99` ausgelöst. Spätere Berichte mit
  100 % oder `FINISH` erzeugen kein Duplikat.
- Layer 1 gilt bei `layer_num >= 2` als abgeschlossen.
- `FAILED` wird neutral als `Druck abgebrochen/fehlgeschlagen` dargestellt.
- P1-MQTT-Berichte sind partiell beziehungsweise Delta-Updates und müssen vor
  der Auswertung tief zusammengeführt werden.
- Die P1S-Kamera verwendet lokal TLS/JPEG auf TCP 6000.
- Telegram-Fotos werden direkt mit `sendPhoto` der Bot API versendet.
- Versandflags bleiben über Containerneustarts hinweg erhalten.
- Der laufende Daemon akzeptiert `/snapshot` und `/snapshop` aus dem
  konfigurierten Telegram-Chat. Er reiht ein aktuelles Kamerabild ein, ohne
  Meilensteinflags zu verändern.

## Docker-Bereitstellung

```text
Container:     bambu-telegram-monitor
Image:         bambu-telegram-monitor:local
Konfiguration: /etc/bambu-telegram/config.yaml
Daten-Volume:  bambu-telegram-data -> /var/lib/bambu-telegram
```

## Telegram-Konfiguration

```yaml
telegram:
  bot_token: "..."
  chat_id: "..."
  caption: "🖨️ {printer}: {milestone} ({progress}%)"
  disable_notification: false
  protect_content: false
  timeout_seconds: 30
```

## Vor Codeänderungen

Folgende Dateien lesen:

1. `docs/AGENTS.md`
2. `README.md`
3. `docs/MANUAL_DOCKER_INSTALL.md`
4. `config.example.yaml`
5. `bambu_monitor.py`

Anschließend ausführen:

```bash
python3 -m py_compile bambu_monitor.py
```

## Nächste sinnvolle Überarbeitung

Die Trigger-Semantik nicht verändern. Ein- und Ausgabe so überarbeiten, dass
der MQTT-Callback nur Statusdaten zusammenführt und Ereignisse einreiht. Kamera-
und Telegram-HTTP-Aufgaben sollen in einer begrenzten Worker-Warteschlange mit
Wiederholungsversuchen und Backoff laufen.

## Zu ergänzende Tests

- Layer 1 zu Layer 2 löst genau einmal aus.
- 49 zu 50 zu 51 % löst die 50-%-Meldung genau einmal aus.
- 99 % bei `RUNNING` ohne vorherigen Wert unter 99 % löst den letzten Snapshot
  nicht aus.
- 98 zu 99 % bei `RUNNING` löst den letzten Snapshot genau einmal aus.
- Ein späteres `FINISH` erzeugt kein Duplikat.
- Wiederholte `PAUSE`-Delta-Berichte erzeugen keine Duplikate.
- `RUNNING` zu `FAILED` löst die Fehlermeldung genau einmal aus.
- Persistierte Versandflags überleben einen Neustart.
- Der Telegram-Client sendet ein Multipart-Foto mit korrekter Chat-ID und
  Bildunterschrift.
