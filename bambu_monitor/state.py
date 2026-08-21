import json
import threading
from pathlib import Path
from typing import Any, Dict

from .common import LOG

class PersistentState:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self):
        try:
            self.data = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.data = {}
        except Exception:
            LOG.exception("Could not load state file %s", self.path)
            self.data = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def printer(self, serial: str) -> dict:
        with self.lock:
            return self.data.setdefault(serial, {})

    def update_printer(self, serial: str, values: dict):
        with self.lock:
            entry = self.data.setdefault(serial, {})
            entry.update(values)
            self.save()


