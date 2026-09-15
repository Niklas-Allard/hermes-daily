"""Kleidungs- und Packempfehlung — reine Heuristik aus Wetter + Stundenplan.

Läuft in der zweiten Welle (`needs_context`), weil es die Ergebnisse der
Netz-Module auswertet statt selbst etwas abzurufen.
"""

from __future__ import annotations

from typing import Any

from .base import Module

DEFAULT_SUBJECT_HINTS = {
    "Sport": "Sportsachen",
    "Schwimmen": "Badesachen",
}


class AdviceModule(Module):
    name = "advice"
    title = "Mitnehmen"
    needs_context = True

    def fetch(self, cfg, ctx) -> dict[str, Any]:
        modules = ctx.get("modules") or {}
        weather = _usable(modules.get("weather"))
        untis = _usable(modules.get("untis"))

        hints: list[str] = []

        if weather:
            hints.extend(_weather_hints(cfg, weather))
        if untis:
            hints.extend(_subject_hints(cfg, untis))

        # Reihenfolge erhalten, Duplikate raus.
        seen = set()
        unique = [h for h in hints if not (h in seen or seen.add(h))]
        return {"hints": unique}

    def render(self, data: dict[str, Any], verbosity: str) -> str:
        hints = data.get("hints") or []
        if not hints:
            return ""
        if verbosity == "compact":
            return "Pack: " + ", ".join(hints)
        return "\n".join(f"- {hint}" for hint in hints)


def _weather_hints(cfg, weather: dict) -> list[str]:
    hints = []
    precip = weather.get("precip_prob") or 0
    temp_min = weather.get("temp_min")
    temp_max = weather.get("temp_max")

    if precip >= 50:
        rain_from = weather.get("rain_from")
        hints.append(f"Regenjacke ({precip}% Regen{f' ab {rain_from}' if rain_from else ''})")
    elif precip >= 30:
        hints.append(f"Schirm einpacken ({precip}% Regen)")

    if temp_min is not None and temp_min <= 0:
        hints.append("Mütze und Handschuhe (Frost)")
    elif temp_min is not None and temp_min <= 5:
        hints.append("warme Jacke")

    if temp_min is not None and temp_max is not None and (temp_max - temp_min) > 12:
        hints.append(f"Zwiebellook ({temp_min}→{temp_max}°C)")

    uv = weather.get("uv_max") or 0
    if uv >= 6:
        hints.append(f"Sonnencreme (UV {uv})")

    if (weather.get("wind_max") or 0) >= 50:
        hints.append(f"windfest anziehen ({weather['wind_max']} km/h)")

    air = weather.get("air") or {}
    if cfg.get_bool("ADVICE_ALLERGY", False):
        strong = [name for name, level in (air.get("pollen") or {}).items() if level == "hoch"]
        if strong:
            hints.append(f"Allergiemittel ({', '.join(strong)} hoch)")

    return hints


def _subject_hints(cfg, untis: dict) -> list[str]:
    mapping = {**DEFAULT_SUBJECT_HINTS, **cfg.get_mapping("ADVICE_SUBJECT_HINTS")}
    hints = []
    for period in untis.get("periods") or []:
        if period.get("status") == "cancelled":
            continue
        subject = period.get("subject") or ""
        for keyword, hint in mapping.items():
            if keyword.lower() in subject.lower():
                hints.append(f"{hint} ({subject}, {period['start']})")
    return hints


def _usable(data: Any) -> dict | None:
    """Nur auswerten, was tatsächlich Daten enthält."""
    if not isinstance(data, dict) or "error" in data or "skipped" in data:
        return None
    return data
