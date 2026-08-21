# Bambu Telegram Monitor – Installation auf einem frischen Raspberry Pi

Die Anleitung ist für Raspberry Pi OS Lite oder ein vergleichbares Debian-System
vorgesehen. Sie verwendet Docker direkt und benötigt kein Docker Compose.

## 1. Am Raspberry Pi anmelden

```bash
ssh <benutzer>@<IP-DES-RASPBERRY-PI>
```

Beispiel:

```bash
ssh pi@192.168.192.100
```

## 2. Raspberry Pi aktualisieren

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Nach dem Neustart erneut per SSH anmelden.

## 3. Git und Docker installieren

```bash
sudo apt update
sudo apt install -y git docker.io ca-certificates
sudo systemctl enable --now docker
```

Docker prüfen:

```bash
sudo docker version
```

## 4. Projekt herunterladen

```bash
cd /opt
sudo git clone https://github.com/sebastianrau/bambu-telegram-monitor.git
sudo chown -R "$USER":"$USER" /opt/bambu-telegram-monitor
cd /opt/bambu-telegram-monitor
```

## 5. Konfiguration erstellen

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

Mindestens folgende Werte eintragen:

- Druckermodell (`model`, zum Beispiel `p1s`)
- IP-Adresse des Druckers
- Seriennummer des Druckers
- Bambu-LAN-Access-Code
- Telegram Bot Token
- Telegram Chat-ID

Messaging-Provider und Drucker werden unabhängig ausgewählt:

```yaml
printers:
  - name: P1S Büro
    model: p1s
    enabled: true
    host: 192.0.2.10
    serial: "SERIAL"
    access_code: "ACCESS-CODE"

messaging:
  provider: telegram
```

Ohne `model` wird aus Kompatibilitätsgründen `p1s` verwendet.

Für Docker müssen diese Pfade verwendet werden:

```yaml
data_dir: /var/lib/bambu-telegram
snapshot_dir: /var/lib/bambu-telegram/snapshots
state_file: /var/lib/bambu-telegram/state.json
snapshot_retention_days: 7
snapshot_cleanup_interval_hours: 6
```

Damit entfernt der laufende Monitor alle sechs Stunden Snapshots, die älter als
sieben Tage sind. `snapshot_retention_days: 0` deaktiviert die automatische
Bereinigung.

Speichern in Nano: `Ctrl+O`, `Enter`, anschließend `Ctrl+X`.

## 6. Konfiguration installieren

```bash
sudo mkdir -p /etc/bambu-telegram
sudo cp config.yaml /etc/bambu-telegram/config.yaml
sudo chown root:10001 /etc/bambu-telegram/config.yaml
sudo chmod 640 /etc/bambu-telegram/config.yaml
```

Die numerische Gruppe `10001` entspricht der Gruppe `bambu-monitor` im
Container. Dadurch kann der unprivilegierte Container-Prozess die nur lesbar
eingebundene Konfiguration öffnen. Andere Benutzer erhalten keinen Zugriff.

## 7. Docker-Image bauen

```bash
cd /opt/bambu-telegram-monitor
sudo docker build -t bambu-telegram-monitor:local .
```

## 8. Telegram-Chat-ID ermitteln

Zuerst muss bei Provider `telegram` unter `telegram.bot_token` der von
BotFather erhaltene Bot-Token in `/etc/bambu-telegram/config.yaml` eingetragen
sein. Die oberste `telegram:`-Sektion ist die kompatible Kurzform; alternativ
können dieselben Werte unter `messaging.telegram` stehen.

Danach das Python-Skript einmalig im Container starten:

```bash
sudo docker run --rm \
  --mount type=bind,src=/etc/bambu-telegram/config.yaml,dst=/etc/bambu-telegram/config.yaml,readonly \
  bambu-telegram-monitor:local \
  --config /etc/bambu-telegram/config.yaml \
  --find-telegram-chat-id \
  --telegram-wait 120
```

Während das Skript wartet, den Telegram-Bot öffnen und folgende Nachricht senden:

```text
/id
```

Der Bot antwortet mit der numerischen Chat-ID. Die ID wird zusätzlich im Terminal ausgegeben. Anschließend die Konfiguration öffnen:

```bash
sudo nano /etc/bambu-telegram/config.yaml
```

Die ermittelte ID unter `telegram.chat_id` eintragen:

```yaml
messaging:
  provider: telegram

telegram:
  bot_token: "BOT-TOKEN"
  chat_id: "123456789"
```

## 9. Persistentes Daten-Volume vorbereiten

```bash
sudo docker volume create bambu-telegram-data

sudo docker run --rm \
  --user root \
  --entrypoint /bin/sh \
  --mount type=volume,src=bambu-telegram-data,dst=/var/lib/bambu-telegram \
  bambu-telegram-monitor:local \
  -c 'mkdir -p /var/lib/bambu-telegram/snapshots && chown -R 10001:10001 /var/lib/bambu-telegram'
```

Das Volume enthält später `state.json` und die gespeicherten Snapshots. Die Konfiguration liegt getrennt unter `/etc/bambu-telegram/config.yaml`.

## 10. Container starten

```bash
sudo docker run -d \
  --name bambu-telegram-monitor \
  --restart unless-stopped \
  --security-opt no-new-privileges:true \
  --mount type=bind,src=/etc/localtime,dst=/etc/localtime,readonly \
  --mount type=bind,src=/etc/timezone,dst=/etc/timezone,readonly \
  --mount type=bind,src=/etc/bambu-telegram/config.yaml,dst=/etc/bambu-telegram/config.yaml,readonly \
  --mount type=volume,src=bambu-telegram-data,dst=/var/lib/bambu-telegram \
  bambu-telegram-monitor:local
```

Der Container startet nach einem Neustart des Raspberry Pi automatisch.
`/etc/localtime` und `/etc/timezone` werden nur lesbar eingebunden, damit die
Zeitstempel im Container dieselbe lokale Zeitzone wie der Raspberry Pi nutzen.

## 11. Funktion prüfen

```bash
sudo docker ps
sudo docker logs -f bambu-telegram-monitor
```

Die Loganzeige wird mit `Ctrl+C` verlassen. Der Container läuft weiter.

Im Log sollten unter anderem diese Meldungen erscheinen:

```text
Monitoring 1 printer(s)
MQTT connected
```

## 12. Manuellen Kamerasnapshot über Telegram testen

Im unter `telegram.chat_id` konfigurierten Chat senden:

```text
/snapshop
```

Alternativ wird auch die korrekte Schreibweise unterstützt:

```text
/snapshot
```

Der Container nimmt ein aktuelles Kamerabild auf und sendet es in denselben
Chat. Bei mehreren aktivierten Druckern kann ein Drucker über einen beliebigen
Teil seines Namens oder den Anfang seiner Seriennummer ausgewählt werden:

```text
/snapshot P1S Büro
/snapshot Büro
/snapshot 01P00A
```

Die Suche ignoriert Groß-/Kleinschreibung. Mehrdeutige Teiltreffer werden nicht
ausgeführt; der Bot gibt stattdessen die passenden Drucker aus.

Nur die konfigurierte Chat-ID ist berechtigt. Die Befehle verwenden Telegram
`getUpdates`; ein Telegram-Webhook darf daher nicht gleichzeitig aktiv sein.
Außerdem darf pro Bot-Token nur eine Monitor-Instanz `getUpdates` verwenden;
andernfalls antwortet Telegram mit HTTP 409. Wer nur ausgehende
Benachrichtigungen benötigt, kann das Polling deaktivieren:

```yaml
telegram:
  commands_enabled: false
```

## Container verwalten

```bash
sudo docker restart bambu-telegram-monitor
sudo docker stop bambu-telegram-monitor
sudo docker start bambu-telegram-monitor
```

## Programm aktualisieren

```bash
cd /opt/bambu-telegram-monitor
git pull
sudo docker build -t bambu-telegram-monitor:local .
sudo docker rm -f bambu-telegram-monitor
```

Danach den Startbefehl aus Abschnitt 10 erneut ausführen. Das Docker-Volume sowie `state.json` und die Snapshots bleiben erhalten.

## Konfiguration ändern

```bash
sudo nano /etc/bambu-telegram/config.yaml
sudo docker restart bambu-telegram-monitor
sudo docker logs -f bambu-telegram-monitor
```
