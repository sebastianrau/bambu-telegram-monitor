from pathlib import Path

import paho.mqtt.client as mqtt

from .p1s import P1SPrinter


class X1Printer(P1SPrinter):
    """X1/X1C adapter; override model-specific behavior here as it diverges."""

    model = "x1"

    def create_mqtt_client(self, client_id: str) -> mqtt.Client:
        return super().create_mqtt_client(client_id)

    def configure_mqtt_client(self, client: mqtt.Client) -> None:
        super().configure_mqtt_client(client)

    def on_mqtt_connected(self, client: mqtt.Client) -> None:
        super().on_mqtt_connected(client)

    def decode_mqtt_message(self, payload: bytes, accumulated_state: dict) -> dict:
        return super().decode_mqtt_message(payload, accumulated_state)

    def capture_snapshot(self, output: Path) -> None:
        super().capture_snapshot(output)
