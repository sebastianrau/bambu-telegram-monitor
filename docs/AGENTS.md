# AGENTS.md

## Projekt

Der Bambu Monitor ist ein Python-Daemon zur Überwachung mehrerer Bambu-Lab-
Drucker in einem vertrauenswürdigen lokalen Netzwerk. Druckermodelle und
Messaging-Ziele sind über Adapter und Registries entkoppelt.

## Architektur

- `bambu_monitor/app.py`: CLI, Start, Signalbehandlung und Shutdown
- `bambu_monitor/printer.py`: gemeinsame Laufzeit, Zustandsautomat und Event-Queue
- `bambu_monitor/printers/`: modellspezifisches MQTT- und Kamera-Handling
- `bambu_monitor/messaging/`: neutrales Messaging-Interface und Provider
- `bambu_monitor/state.py`: persistente Ereignisflags
- `bambu_monitor/snapshots.py`: Aufbewahrung und Bereinigung
- `bambu_monitor/diagnostics.py`: MQTT- und Kamera-Verbindungstests
- `bambu_monitor.py`: kompatibler CLI-Starter

Der gemeinsame `PrinterRuntime` darf keine modellspezifischen Topics,
Authentifizierungsverfahren oder Kameraprotokolle enthalten. Jeder
Druckeradapter verantwortet:

1. Erzeugen und Konfigurieren seines MQTT-Clients
2. MQTT-Verbindung, Topics und initiale Statusabfrage
3. Dekodieren und Normalisieren eingehender Berichte
4. Erzeugen eines Snapshots

Ausgehende Benachrichtigungen laufen ausschließlich über `MessageClient`.
Provider-spezifische APIs dürfen nicht in Druckeradapter oder Runtime gelangen.

## Verbindliche Trigger-Semantik

Nicht ohne ausdrückliche Anforderung ändern:

- `layer1`: `layer_num >= 2`
- `started`: neu erkannter Auftrag in `PREPARE` oder `RUNNING`; keine Meldung,
  wenn die erste persistierte Beobachtung bereits einen aktiven Druck zeigt
- `progress50`: erstes `mc_percent >= 50`
- `finished`: Übergang von einem zuvor beobachteten `mc_percent < 99` zu
  `mc_percent >= 99`; spätere 100-%- oder `FINISH`-Berichte erzeugen kein Duplikat
- `pause`: Übergang zu `gcode_state == "PAUSE"`
- `failed`: `gcode_state == "FAILED"`
- Text für `FAILED`: `Druck abgebrochen/fehlgeschlagen`

MQTT-Berichte können Delta-Updates sein. Das Zusammenführen erfolgt im
zuständigen Druckeradapter vor der Auswertung.

## Druckeradapter

Registrierung erfolgt in `bambu_monitor/printers/registry.py`. Aktuelle
Modellschlüssel: `p1`, `p1s`, `p2s`, `x1`, `x1c`.

`P2SPrinter` und `X1Printer` besitzen eigene Erweiterungspunkte, verwenden
aktuell aber noch das Verhalten von `P1SPrinter`. Abweichende MQTT- oder
Kameraprotokolle müssen in den jeweiligen Dateien implementiert werden.

### P1S-Protokoll

- MQTT/TLS auf TCP 8883
- Benutzername `bblp`, LAN-Access-Code als Passwort
- `device/<serial>/report` und `device/<serial>/request`
- selbstsigniertes Druckerzertifikat
- Kamera über TLS auf TCP 6000
- P1/A1-JPEG-Frame-Protokoll

Rückwärtsentwickelten Protokollcode in den Druckeradaptern isoliert halten.

## Messaging

Providerwahl:

```yaml
messaging:
  provider: telegram
```

Die bisherige oberste `telegram:`-Sektion bleibt kompatibel. Alternativ werden
Einstellungen unter `messaging.telegram` unterstützt. Neue Provider erhalten
eine eigene Datei unter `bambu_monitor/messaging/` und einen Eintrag in
`messaging/registry.py`.

Telegram verwendet `sendPhoto` für Bilder und optional `getUpdates` für
`/snapshot` sowie `/snapshop`. Nur die konfigurierte `chat_id` ist
berechtigt. Pro Bot darf nur ein `getUpdates`-Consumer laufen; gleichzeitig
darf kein Webhook aktiv sein.

Manuelle Snapshots verwenden die begrenzte Event-Queue und verändern keine
persistenten Meilensteinflags. Mehrdeutige Druckerauswahlen werden abgelehnt.

## Sicherheit

Niemals committen oder protokollieren:

- Bambu-LAN-Access-Codes
- Messaging-Zugangsdaten wie Telegram-Bot-Token
- echte produktive `config.yaml`

## Validierung

```bash
python3 -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/tmp/bambu-pycache python3 -m compileall -q \
  bambu_monitor.py bambu_monitor tests
python3 bambu_monitor.py --help
python3 -m bambu_monitor --help
```

## Wichtige Dateien

- `bambu_monitor.py`
- `bambu_monitor/`
- `config.example.yaml`
- `Dockerfile`
- `README.md`
- `docs/MANUAL_DOCKER_INSTALL.md`
- `docs/CODEX_HANDOFF.md`
- `requirements.txt`
- `tests/`
