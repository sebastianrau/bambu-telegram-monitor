from .base import BambuPrinter
from .p1s import P1SPrinter
from .p2s import P2SPrinter
from .x1 import X1Printer


_MODELS: dict[str, type[BambuPrinter]] = {
    "p1s": P1SPrinter,
    "p1": P1SPrinter,
    "p2s": P2SPrinter,
    "x1": X1Printer,
    "x1c": X1Printer,
}


def supported_models() -> tuple[str, ...]:
    return tuple(sorted(_MODELS))


def create_printer(cfg: dict) -> BambuPrinter:
    model = str(cfg.get("model", "p1s")).strip().lower()
    try:
        printer_class = _MODELS[model]
    except KeyError as exc:
        choices = ", ".join(supported_models())
        raise ValueError(f"Unsupported printer model {model!r}; choose one of: {choices}") from exc
    return printer_class(cfg)
