"""Markdown-Renderer mit drei Ausführlichkeitsstufen.

Die Verbosity greift ausschließlich hier — `--format json` gibt immer die
vollständige Datenstruktur aus, damit das Maschinenformat verlustfrei bleibt.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from .config import MODULE_ORDER
from .runner import MODULES_BY_NAME

WEEKDAYS_DE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
WEEKDAYS_DE_LONG = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)


def render_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


def render_markdown(result: dict[str, Any], verbosity: str, max_chars: int = 0) -> str:
    date = dt.date.fromisoformat(result["date"])
    sections: list[str] = [_header(date, verbosity)]

    for name in MODULE_ORDER:
        data = result["modules"].get(name)
        if data is None:
            continue
        body = _render_section(name, data, verbosity)
        if body:
            sections.append(body)

    diff = result.get("diff")
    if diff:
        sections.append(_render_diff(diff, verbosity))

    # compact packt die Abschnitte dicht — Leerzeilen sind hier reine Token-Kosten.
    separator = "\n" if verbosity == "compact" else "\n\n"
    text = separator.join(part for part in sections if part).strip() + "\n"
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 4].rstrip() + "\n...\n"
    return text


def _header(date: dt.date, verbosity: str) -> str:
    if verbosity == "compact":
        return f"{date.strftime('%d.%m.')} {WEEKDAYS_DE[date.weekday()]}"
    long_date = f"{WEEKDAYS_DE_LONG[date.weekday()]}, {date.strftime('%d.%m.%Y')}"
    return f"# Tagescheckup — {long_date}"


def _render_section(name: str, data: dict[str, Any], verbosity: str) -> str:
    module = MODULES_BY_NAME.get(name)
    if module is None:
        return ""

    if "skipped" in data:
        # Bewusst abgeschaltete/unkonfigurierte Module bleiben still.
        return "" if verbosity != "full" else _titled(module, f"_übersprungen: {data['skipped']}_", verbosity)

    if "error" in data:
        # requests-Fehler enthalten die komplette URL samt Parametern — im
        # compact-Modus kostet das mehr Tokens als der gesamte übrige Checkup.
        message = _shorten_error(data["error"], 90 if verbosity == "compact" else 300)
        if verbosity == "compact":
            return f"{module.title}: FEHLER ({message})"
        return _titled(module, f"⚠️ Fehler: {message}", verbosity)

    body = module.render(data, verbosity)
    if not body or not body.strip():
        return ""
    if verbosity == "compact":
        return body.strip()
    return _titled(module, body.strip(), verbosity)


def _shorten_error(message: str, limit: int) -> str:
    text = " ".join(str(message).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _titled(module, body: str, verbosity: str) -> str:
    return f"## {module.title}\n{body}"


def _render_diff(diff: dict[str, Any], verbosity: str) -> str:
    lines = diff.get("lines") or []
    if not lines:
        return ""
    if verbosity == "compact":
        return "Änderungen: " + "; ".join(lines)
    return "## Änderungen seit dem letzten Abruf\n" + "\n".join(f"- {line}" for line in lines)
