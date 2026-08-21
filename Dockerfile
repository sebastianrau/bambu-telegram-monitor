FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg is required by the planned P2S/X1/H2 RTSPS camera adapter.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/bambu-telegram

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY bambu_monitor.py ./
COPY bambu_monitor ./bambu_monitor

RUN groupadd --system --gid 10001 bambu-monitor \
    && useradd --system --uid 10001 --gid bambu-monitor \
        --home-dir /var/lib/bambu-telegram --create-home bambu-monitor \
    && mkdir -p /var/lib/bambu-telegram/snapshots \
    && chown -R bambu-monitor:bambu-monitor /var/lib/bambu-telegram

USER bambu-monitor

VOLUME ["/var/lib/bambu-telegram"]

ENTRYPOINT ["python", "/opt/bambu-telegram/bambu_monitor.py"]
CMD ["--config", "/etc/bambu-telegram/config.yaml"]
