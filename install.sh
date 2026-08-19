#!/bin/sh
set -eu

APP=/opt/bambu-telegram
CFG=/etc/bambu-telegram
DATA=/var/lib/bambu-telegram
SERVICE=bambu-telegram.service

if ! id bambu-monitor >/dev/null 2>&1; then
    useradd --system --home "$DATA" --shell /usr/sbin/nologin bambu-monitor
fi

mkdir -p "$APP" "$CFG" "$DATA"

cp bambu_monitor.py "$APP/"
cp requirements.txt "$APP/"

if [ ! -f "$CFG/config.yaml" ]; then
    cp config.example.yaml "$CFG/config.yaml"
fi

cp "$SERVICE" /etc/systemd/system/

python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --upgrade pip
"$APP/venv/bin/pip" install -r "$APP/requirements.txt"

chown -R root:root "$APP" "$CFG"
chown -R bambu-monitor:bambu-monitor "$DATA"
chown root:bambu-monitor "$CFG/config.yaml"
chmod 750 "$CFG"
chmod 640 "$CFG/config.yaml"

systemctl daemon-reload
systemctl enable "$SERVICE"

echo
echo "Edit: $CFG/config.yaml"
echo "Then: systemctl start bambu-telegram"
echo "Logs: journalctl -u bambu-telegram -f"
