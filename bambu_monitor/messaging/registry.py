from .base import MessageClient
from .telegram import TelegramClient, TelegramCommandPoller


def configured_provider(cfg: dict) -> str:
    messaging = cfg.get("messaging")
    if isinstance(messaging, dict):
        return str(messaging.get("provider", "telegram")).strip().lower()
    return "telegram"


def supported_providers() -> tuple[str, ...]:
    return ("telegram",)


def provider_config(cfg: dict, provider: str) -> dict:
    messaging = cfg.get("messaging")
    nested = messaging.get(provider) if isinstance(messaging, dict) else None
    legacy = cfg.get(provider)
    result = nested or legacy
    if not isinstance(result, dict):
        raise ValueError(f"{provider} messaging configuration is missing")
    return result


def create_message_client(cfg: dict) -> MessageClient:
    provider = configured_provider(cfg)
    if provider == "telegram":
        return TelegramClient(provider_config(cfg, provider))
    raise ValueError(f"Unsupported messaging provider {provider!r}")


def create_command_poller(cfg: dict, client: MessageClient, runtimes: list):
    provider = configured_provider(cfg)
    if provider == "telegram" and isinstance(client, TelegramClient):
        return TelegramCommandPoller(provider_config(cfg, provider), client, runtimes)
    return None
