import logging
import threading
from typing import Optional

LOG = logging.getLogger("bambu-monitor")
STOP = threading.Event()

def deep_merge(dst: dict, src: dict) -> dict:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def as_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "n/a"
    return f"{max(0.0, seconds):.1f}s"


def mqtt_properties_summary(properties) -> str:
    if properties is None:
        return "none"
    details = []
    for attribute in ("ReasonString", "ServerReference"):
        value = getattr(properties, attribute, None)
        if value:
            details.append(f"{attribute}={value}")
    return ",".join(details) if details else "none"


