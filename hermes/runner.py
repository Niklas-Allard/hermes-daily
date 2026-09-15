"""Führt die Module aus — parallel, mit Timeout und harter Fehlerisolierung.

Ein Morgen-Tool darf nie komplett scheitern, weil ein RSS-Feed hustet. Deshalb
landet jede Exception als ``{"error": ...}`` im Slot des Moduls und alle anderen
Abschnitte werden trotzdem gerendert.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Any

from .config import MODULE_ORDER
from .modules.advice import AdviceModule
from .modules.calendar_ import CalendarModule
from .modules.news import NewsModule
from .modules.tasks import TasksModule
from .modules.transit import TransitModule
from .modules.untis import UntisModule
from .modules.weather import WeatherModule

ALL_MODULES = [
    WeatherModule(),
    UntisModule(),
    CalendarModule(),
    NewsModule(),
    TasksModule(),
    TransitModule(),
    AdviceModule(),
]

MODULES_BY_NAME = {module.name: module for module in ALL_MODULES}


def select_modules(cfg, only: list[str] | None, skip: list[str] | None) -> list:
    """Filtert die Modulliste über .env-Flags und die CLI-Schalter."""
    selected = []
    for name in MODULE_ORDER:
        module = MODULES_BY_NAME.get(name)
        if module is None:
            continue
        if only and name not in only:
            continue
        if skip and name in skip:
            continue
        # --only hebt das .env-Flag bewusst auf: ein gezielter Aufruf soll auch
        # dann funktionieren, wenn das Modul im Alltag abgeschaltet ist.
        if not only and not module.enabled(cfg):
            continue
        selected.append(module)
    return selected


def run(cfg, modules: list, target_date: dt.date, extra: dict | None = None) -> dict[str, Any]:
    """Sammelt alle Moduldaten ein und gibt das Ergebnis-Dict zurück."""
    ctx: dict[str, Any] = {
        "date": target_date.isoformat(),
        "weekday": target_date.strftime("%A"),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "modules": {},
    }
    ctx.update(extra or {})

    network_modules = [m for m in modules if not m.needs_context]
    context_modules = [m for m in modules if m.needs_context]

    if network_modules:
        results: dict[str, Any] = {}
        budget = cfg.timeout * 2 + 5

        # Bewusst rohe Daemon-Threads statt ThreadPoolExecutor: dessen
        # atexit-Handler joint alle Worker und würde den Prozess trotz
        # abgelaufener Frist an einem hängenden Request festhalten.
        threads = []
        for module in network_modules:
            thread = threading.Thread(
                target=_collect,
                args=(results, module, cfg, ctx, target_date),
                name=f"hermes-{module.name}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        deadline = time.monotonic() + budget
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

        for module in network_modules:
            ctx["modules"][module.name] = results.get(
                module.name, {"error": f"Zeitüberschreitung nach {budget}s"}
            )

    # Zweite Welle: Module, die auf die Ergebnisse der ersten aufbauen.
    for module in context_modules:
        ctx["modules"][module.name] = _safe_fetch(module, cfg, ctx, target_date)

    return ctx


def _collect(results: dict, module, cfg, ctx: dict, target_date: dt.date) -> None:
    """Thread-Einstiegspunkt — schreibt das Ergebnis unter dem Modulnamen ab."""
    results[module.name] = _safe_fetch(module, cfg, ctx, target_date)


def _safe_fetch(module, cfg, ctx: dict, target_date: dt.date) -> dict[str, Any]:
    ok, hint = module.configured(cfg)
    if not ok:
        return {"skipped": hint or "nicht konfiguriert"}
    try:
        return module.fetch(cfg, {**ctx, "target_date": target_date})
    except Exception as exc:  # noqa: BLE001 - Fehlerisolierung ist hier der Zweck
        return {"error": f"{type(exc).__name__}: {exc}"}
