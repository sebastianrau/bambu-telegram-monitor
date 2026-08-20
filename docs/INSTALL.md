# Bambu P1S Telegram Monitor

## Installation und Konfiguration

Der Bambu P1S Telegram Monitor wird ausschließlich als Docker-Container
installiert und betrieben. Andere Bereitstellungsarten werden nicht
unterstützt.

Die vollständige Anleitung enthält:

- Installation von Docker
- Download und Aktualisierung des Projekts
- Erstellung und Absicherung der Konfiguration
- Ermittlung der Telegram-Chat-ID
- Vorbereitung des persistenten Docker-Volumes
- Image-Build und Containerstart
- Logs, Kameratest und Wartung

Weiter mit der
[manuellen Docker-Installation](MANUAL_DOCKER_INSTALL.md).

## Laufzeitdaten

Die Konfiguration wird vom Host nur lesbar eingebunden:

```text
/etc/bambu-telegram/config.yaml
```

Status und Snapshots liegen im persistenten Docker-Volume:

```text
bambu-telegram-data
```

Der Container startet durch `--restart unless-stopped` nach einem Neustart des
Hosts automatisch. Verwaltung und Logs erfolgen ausschließlich mit Docker.
