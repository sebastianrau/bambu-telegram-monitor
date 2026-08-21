from abc import ABC, abstractmethod
from pathlib import Path
import paho.mqtt.client as mqtt


class BambuPrinter(ABC):
    """Model-specific printer operations used by the shared monitor runtime."""

    model = "generic"
    mqtt_port = 8883

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @property
    def host(self) -> str:
        return str(self.cfg["host"])

    @property
    def serial(self) -> str:
        return str(self.cfg["serial"])

    @property
    def access_code(self) -> str:
        return str(self.cfg["access_code"])

    @abstractmethod
    def create_mqtt_client(self, client_id: str) -> mqtt.Client:
        """Create and configure the MQTT client for this model."""

    @abstractmethod
    def configure_mqtt_client(self, client: mqtt.Client) -> None:
        """Apply model-specific authentication, TLS and reconnect settings."""

    @abstractmethod
    def on_mqtt_connected(self, client: mqtt.Client) -> None:
        """Subscribe to model topics and request the initial printer state."""

    @abstractmethod
    def decode_mqtt_message(self, payload: bytes, accumulated_state: dict) -> dict:
        """Merge/decode a report and return its normalized print state."""

    def connect_mqtt(self, client: mqtt.Client) -> None:
        client.connect_async(self.host, self.mqtt_port, keepalive=30)

    def connect_mqtt_for_test(self, client: mqtt.Client) -> None:
        client.connect(self.host, self.mqtt_port, keepalive=30)

    @abstractmethod
    def capture_snapshot(self, output: Path) -> None:
        """Capture one camera frame and save it as a JPEG."""
