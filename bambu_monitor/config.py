import logging
from pathlib import Path

import yaml

from .printers import supported_models
from .messaging.registry import configured_provider, provider_config, supported_providers

def load_config(path: Path, require_telegram: bool = True,
                require_chat_id: bool = True) -> dict:
    cfg = yaml.safe_load(path.read_text())
    if not isinstance(cfg, dict):
        raise ValueError("Config must be a YAML mapping")
    printers = cfg.get("printers")
    if not isinstance(printers, list) or not printers:
        raise ValueError("At least one printer must be configured")
    for index, printer in enumerate(printers, start=1):
        if not isinstance(printer, dict):
            raise ValueError(f"Printer {index} must be a YAML mapping")
        if printer.get("enabled", True):
            for key in ("host", "serial", "access_code"):
                if not printer.get(key):
                    raise ValueError(f"Enabled printer {index} is missing {key}")
            model = str(printer.get("model", "p1s")).strip().lower()
            if model not in supported_models():
                choices = ", ".join(supported_models())
                raise ValueError(
                    f"Enabled printer {index} has unsupported model {model!r}; "
                    f"choose one of: {choices}"
                )
        for key in ("event_queue_size", "delivery_attempts"):
            if key in printer and int(printer[key]) < 1:
                raise ValueError(f"Printer {index} {key} must be at least 1")
        if "camera_warmup_frames" in printer and int(printer["camera_warmup_frames"]) < 0:
            raise ValueError(f"Printer {index} camera_warmup_frames must not be negative")
    if not any(printer.get("enabled", True) for printer in printers):
        raise ValueError("At least one printer must be enabled")

    for key in ("snapshot_retention_days", "snapshot_cleanup_interval_hours"):
        if key in cfg and float(cfg[key]) < 0:
            raise ValueError(f"{key} must not be negative")

    if require_telegram:
        provider = configured_provider(cfg)
        if provider not in supported_providers():
            choices = ", ".join(supported_providers())
            raise ValueError(
                f"Unsupported messaging provider {provider!r}; choose one of: {choices}"
            )
        telegram = provider_config(cfg, provider)
        required_keys = ("bot_token", "chat_id") if require_chat_id else ("bot_token",)
        for key in required_keys:
            if not telegram.get(key):
                raise ValueError(f"telegram is missing {key}")
        if int(telegram.get("command_poll_timeout_seconds", 20)) < 1:
            raise ValueError("telegram command_poll_timeout_seconds must be at least 1")
        if float(telegram.get("command_cooldown_seconds", 10)) < 0:
            raise ValueError("telegram command_cooldown_seconds must not be negative")
    return cfg


def configure_logging(cfg: dict):
    level = getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

