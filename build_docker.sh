#!/bin/bash

set -euo pipefail

git pull

sudo docker build -t bambu-telegram-monitor:local .

sudo docker rm -f bambu-telegram-monitor 2>/dev/null || true

sudo docker run -d \
  --name bambu-telegram-monitor \
  --restart unless-stopped \
  --security-opt no-new-privileges:true \
  --mount type=bind,src=/etc/localtime,dst=/etc/localtime,readonly \
  --mount type=bind,src=/etc/bambu-telegram/config.yaml,dst=/etc/bambu-telegram/config.yaml,readonly \
  --mount type=volume,src=bambu-telegram-data,dst=/var/lib/bambu-telegram \
  bambu-telegram-monitor:local