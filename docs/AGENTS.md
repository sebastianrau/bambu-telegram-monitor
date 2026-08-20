# AGENTS.md

## Projekt

Der Bambu P1S Telegram Monitor ist ein Python-Daemon zur Überwachung mehrerer
Bambu-Lab-P1S-Drucker in einem vertrauenswürdigen lokalen Netzwerk. Bei
Druckereignissen sendet er Snapshots über die Telegram Bot API.

## Kernablauf

1. Verbindung zu jedem konfigurierten Drucker über MQTT/TLS auf TCP 8883.
2. Tiefes Zusammenführen partieller MQTT-Berichte mit dem gesammelten Druckerstatus.
3. Erkennen konfigurierter Meilensteine und Statusübergänge.
4. Aufnehmen eines JPEGs über das lokale TLS-Kameraprotokoll auf TCP 6000.
5. Senden des JPEGs über `sendPhoto` der Telegram Bot API.
6. Persistieren versendeter Ereignisse, um Duplikate nach Neustarts zu vermeiden.

## Verbindliche Trigger-Semantik

Nicht ohne ausdrückliche Anforderung ändern:

- `layer1`: `layer_num >= 2`
- `started`: neu erkannter Auftrag in `PREPARE` oder `RUNNING`; keine Meldung,
  wenn die erste persistierte Beobachtung bereits einen aktiven Druck zeigt
- `progress50`: erstes `mc_percent >= 50`
- `finished`: Übergang von einem zuvor beobachteten `mc_percent < 99` zu
  `mc_percent >= 99`; spätere Berichte mit 100 % oder `FINISH` dürfen keine
  doppelte Meldung auslösen
- `pause`: Übergang zu `gcode_state == "PAUSE"`
- `failed`: `gcode_state == "FAILED"`
- Text für `FAILED`: `Druck abgebrochen/fehlgeschlagen`

MQTT-Nachrichten können Delta-Updates sein. Vor der Auswertung immer
zusammenführen.

## Telegram

Konfiguration:

```yaml
telegram:
  bot_token: "..."
  chat_id: "..."
  caption: "🖨️ {printer}: {milestone} ({progress}%)"
```

Die aktuelle Implementierung ruft Folgendes auf:

```text
POST https://api.telegram.org/bot<TOKEN>/sendPhoto
```

Das JPEG wird als `multipart/form-data` hochgeladen.

Der laufende Daemon verwendet Telegram `getUpdates` für `/snapshot` und den
historischen, vom Benutzer gewünschten Alias `/snapshop`. Nur die konfigurierte
`chat_id` ist berechtigt. Manuelle Snapshots müssen die begrenzte
Drucker-Ereigniswarteschlange verwenden und dürfen persistente Meilensteinflags
nicht verändern. Druckerauswahlen stimmen ohne Beachtung der Groß-/Kleinschreibung
mit einem beliebigen Teil des konfigurierten Namens oder dem Anfang der
Seriennummer überein. Mehrdeutige Teiltreffer dürfen nicht ausgeführt werden.

Den Bot-Token niemals protokollieren.

## Protokollannahmen

### Bambu MQTT

- TLS auf TCP 8883
- Benutzername `bblp`
- Passwort ist der LAN-Access-Code
- `device/<serial>/report`
- `device/<serial>/request`
- selbstsigniertes Druckerzertifikat

### P1S-Kamera

- TLS auf TCP 6000
- P1/A1-JPEG-Frame-Protokoll
- 80-Byte-Authentifizierungspaket
- 16-Byte-Frame-Header mit anschließendem JPEG

Rückwärtsentwickelten Code für das Druckerprotokoll isoliert halten.

## Sicherheit

Niemals committen:

- Bambu-LAN-Access-Codes
- Telegram-Bot-Token
- echte produktive `config.yaml`

## Empfohlene technische Verbesserungen

1. Status- und Meilensteinübergänge mit Unit-Tests absichern.
2. Kamera- und Telegram-HTTP-Arbeit aus dem MQTT-Callback in eine begrenzte
   Worker-Warteschlange verschieben.
3. Deterministische Ereignis-IDs ergänzen.
4. Wiederholungsversuche mit Backoff ergänzen.
5. Konfiguration beim Start validieren.
6. CLI-Tests für MQTT, Kamera und Telegram ergänzen.
7. Docker-Secrets oder Umgebungsvariablen für Geheimnisse unterstützen.

## Validierung

```bash
python3 -m py_compile bambu_monitor.py
```

Wenn Tests vorhanden sind:

```bash
python3 -m unittest discover -v
```

## Dateien

- `bambu_monitor.py`
- `config.example.yaml`
- `Dockerfile`
- `update.sh`
- `docs/MANUAL_DOCKER_INSTALL.md`
- `requirements.txt`
- `README.md`
- `docs/CODEX_HANDOFF.md`
