# Codex-Übergabe

## Ziel und aktueller Stand

Der Bambu Monitor überwacht mehrere konfigurierte Bambu-Lab-Drucker, erkennt
Druckereignisse, erzeugt modellspezifische Kamerasnapshots und sendet sie über
einen austauschbaren Messaging-Provider.

Aktuell implementiert:

1. Start des Druckauftrags
2. Abschluss von Layer 1
3. 50 % Druckfortschritt
4. Erreichen von 99 % Druckfortschritt
5. Pause
6. Fehler oder Abbruch
7. Manueller Snapshot über Telegram

## Architekturentscheidungen

- `PrinterRuntime` enthält nur gemeinsame Laufzeit, Zustandsautomat,
  Event-Queue, Wiederholungsversuche und Zustellung.
- Jeder Adapter in `bambu_monitor/printers/` besitzt MQTT-Erzeugung,
  Authentifizierung, Topics, Statusdekodierung und Snapshot-Erzeugung.
- `P1SPrinter` implementiert derzeit das konkrete P1/A1-LAN-Protokoll.
- `P2SPrinter` und `X1Printer` sind getrennte Adapter, delegieren aktuell
  jedoch noch an `P1SPrinter`. Abweichende Protokolle dort ergänzen.
- `MessageClient` entkoppelt die Runtime vom Provider.
- Telegram befindet sich in `bambu_monitor/messaging/telegram.py`.
- Neue Provider wie Slack werden als eigener Adapter plus Registry-Eintrag
  ergänzt; Änderungen an Druckeradaptern sind dafür nicht erforderlich.
- `bambu_monitor.py` bleibt ein kompatibler Starter; die Anwendung liegt im
  Paket `bambu_monitor/`.

## Trigger-Semantik

- Der letzte Snapshot wird beim Übergang von einem beobachteten
  `mc_percent < 99` zu `mc_percent >= 99` ausgelöst.
- Spätere Berichte mit 100 % oder `FINISH` erzeugen kein Duplikat.
- Layer 1 gilt bei `layer_num >= 2` als abgeschlossen.
- `FAILED` wird als `Druck abgebrochen/fehlgeschlagen` dargestellt.
- Erste Beobachtungen eines bereits aktiven oder abgeschlossenen Drucks werden
  als Baseline behandelt.
- Persistente Flags verhindern Duplikate nach Neustarts.

Diese Semantik nicht nebenbei verändern.

## Konfiguration

Druckermodell:

```yaml
printers:
  - name: P1S Büro
    model: p1s
    host: 192.0.2.10
    serial: "SERIAL"
    access_code: "ACCESS-CODE"
```

Messaging-Provider mit kompatibler Telegram-Sektion:

```yaml
messaging:
  provider: telegram

telegram:
  bot_token: "..."
  chat_id: "..."
  commands_enabled: true
```

Provider-Einstellungen können alternativ unter `messaging.telegram` liegen.

## Docker-Bereitstellung

```text
Container:     bambu-telegram-monitor
Image:         bambu-telegram-monitor:local
Konfiguration: /etc/bambu-telegram/config.yaml
Daten-Volume:  bambu-telegram-data -> /var/lib/bambu-telegram
Einstieg:      python /opt/bambu-telegram/bambu_monitor.py
```

Der Docker-Build kopiert sowohl den Starter als auch das Paket
`bambu_monitor/`.

## Vor Codeänderungen

Lesen:

1. `docs/AGENTS.md`
2. `README.md`
3. `docs/MANUAL_DOCKER_INSTALL.md`
4. `config.example.yaml`
5. betroffene Module unter `bambu_monitor/`

Validieren:

```bash
python3 -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/tmp/bambu-pycache python3 -m compileall -q \
  bambu_monitor.py bambu_monitor tests
```

## Nächste sinnvolle Erweiterungen

- Reale P2S- und X1/X1C-Protokollunterschiede in deren Adaptern implementieren
  und mit Hardware/Fixtures testen.
- Slack als zweiten `MessageClient` implementieren.
- Provider-spezifische Konfigurationsvalidierung weiter ausbauen.
- Deterministische Ereignis-IDs ergänzen.
- Geheimnisse über Docker-Secrets oder Umgebungsvariablen unterstützen.

## Testabdeckung

Die Tests decken Zustandsübergänge, persistente Flags, Telegram-Fotoversand,
Telegram-Kommandos, Konfigurationsvalidierung, Snapshot-Bereinigung,
Verbindungstest, Drucker-Registry, MQTT-Dekodierung und Messaging-Registry ab.
