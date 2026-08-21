import json
import socket
import ssl
import struct
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from .base import BambuPrinter
from ..common import deep_merge


def recv_exact(sock: ssl.SSLSocket, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            raise ConnectionError("Camera connection closed")
        buf.extend(chunk)
    return bytes(buf)


def capture_snapshot(host: str, access_code: str, output: Path,
                     timeout: float = 10.0, warmup_frames: int = 2) -> None:
    """Capture a JPEG via the P1/A1 TLS camera protocol on TCP port 6000."""
    output.parent.mkdir(parents=True, exist_ok=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    raw = socket.create_connection((host, 6000), timeout=timeout)
    sock = context.wrap_socket(raw, server_hostname=host)
    sock.settimeout(timeout)
    try:
        auth = bytearray(80)
        struct.pack_into("<I", auth, 0, 0x40)
        struct.pack_into("<I", auth, 4, 0x3000)
        auth[16:20] = b"bblp"
        code = access_code.encode("utf-8")[:31]
        auth[48:48 + len(code)] = code
        sock.sendall(auth)

        deadline = time.time() + timeout
        valid_frames = 0
        while time.time() < deadline:
            header = recv_exact(sock, 16)
            payload_size = struct.unpack_from("<I", header, 0)[0]
            if payload_size <= 0 or payload_size > 20 * 1024 * 1024:
                raise RuntimeError(f"Invalid camera payload size: {payload_size}")
            payload = recv_exact(sock, payload_size)
            if payload.startswith(b"\xff\xd8") and payload.endswith(b"\xff\xd9"):
                if valid_frames < warmup_frames:
                    valid_frames += 1
                    continue
                output.write_bytes(payload)
                return
        raise TimeoutError("No JPEG frame received from printer camera")
    finally:
        sock.close()


class P1SPrinter(BambuPrinter):
    model = "p1s"

    def create_mqtt_client(self, client_id: str) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        self.configure_mqtt_client(client)
        return client

    def configure_mqtt_client(self, client: mqtt.Client) -> None:
        client.username_pw_set("bblp", self.access_code)
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.reconnect_delay_set(
            int(self.cfg.get("mqtt_reconnect_min_seconds", 2)),
            int(self.cfg.get("mqtt_reconnect_max_seconds", 60)),
        )

    def on_mqtt_connected(self, client: mqtt.Client) -> None:
        client.subscribe(f"device/{self.serial}/report", qos=0)
        request = {
            "pushing": {
                "sequence_id": "0",
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        }
        client.publish(
            f"device/{self.serial}/request", json.dumps(request), qos=0
        )

    def decode_mqtt_message(self, payload: bytes, accumulated_state: dict) -> dict:
        report = json.loads(payload.decode("utf-8"))
        deep_merge(accumulated_state, report)
        return accumulated_state.get("print", {})

    def capture_snapshot(self, output: Path) -> None:
        capture_snapshot(
            self.host,
            self.access_code,
            output,
            timeout=float(self.cfg.get("camera_timeout_seconds", 10)),
            warmup_frames=int(self.cfg.get("camera_warmup_frames", 2)),
        )
