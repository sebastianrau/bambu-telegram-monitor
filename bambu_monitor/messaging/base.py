from abc import ABC, abstractmethod
from pathlib import Path


class MessageClient(ABC):
    """Outbound notification channel used by printer runtimes."""

    provider = "generic"

    @abstractmethod
    def send_image(self, image_path: Path, printer_name: str,
                   milestone: str, progress: int) -> None:
        """Send a printer snapshot notification."""
