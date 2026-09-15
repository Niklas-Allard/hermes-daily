"""Nächste Abfahrten — zwei Provider hinter einer gemeinsamen Schnittstelle.

`dbf` (dbf.finalrewind.org) läuft bundesweit für DB-Stationen und braucht nur
den Stationsnamen. `hafas_rest` spricht jede transport.rest-Instanz an und
deckt auch Busse/Trams ab, benötigt aber eine Stop-ID.

Stand der DB-Instanz: `v6.db.transport.rest` antwortet derzeit mit 503, weil das
HAFAS-Backend abgeschaltet wurde. Deshalb ist `dbf` der Default.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .base import Module, http_get_json

DBF_URL = "https://dbf.finalrewind.org/{station}.json"


class TransitModule(Module):
    name = "transit"
    title = "Abfahrten"

    def configured(self, cfg) -> tuple[bool, str]:
        provider = _provider(cfg)
        if provider == "dbf":
            if not cfg.get("TRANSIT_STATION"):
                return False, "TRANSIT_STATION fehlt (Stationsname, z. B. 'Stuttgart Hbf')"
        elif provider == "hafas_rest":
            if not cfg.get("TRANSIT_STOP_ID"):
                return False, (
                    'TRANSIT_STOP_ID fehlt (ermitteln mit --transit-search "Haltestelle")'
                )
        else:
            return False, f"unbekannter TRANSIT_PROVIDER: {provider}"
        return True, ""

    def fetch(self, cfg, ctx) -> dict[str, Any]:
        # Abfahrten sind nur für heute sinnvoll — bei --date in der Zukunft still bleiben.
        if ctx["target_date"] != dt.date.today():
            return {"skipped": "Abfahrten gibt es nur für heute"}

        provider = _provider(cfg)
        departures = _fetch_dbf(cfg) if provider == "dbf" else _fetch_hafas(cfg)
        departures = _filter(cfg, departures)
        return {
            "provider": provider,
            "station": cfg.get("TRANSIT_STATION") or cfg.get("TRANSIT_STOP_ID"),
            "departures": departures[: cfg.get_int("TRANSIT_MAX", 5)],
        }

    def render(self, data: dict[str, Any], verbosity: str) -> str:
        departures = data.get("departures") or []
        if not departures:
            return "" if verbosity == "compact" else "- Keine passenden Abfahrten gefunden."

        if verbosity == "compact":
            parts = [
                f"{d['time']}{_delay_suffix(d)} {d['line']}→{_truncate(d['destination'], 16)}"
                for d in departures
            ]
            return "Abfahrten: " + " | ".join(parts)

        lines = []
        for dep in departures:
            line = f"{dep['time']}{_delay_suffix(dep)} · {dep['line']} → {dep['destination']}"
            if dep.get("platform"):
                line += f" (Gl. {dep['platform']})"
            lines.append(f"- {line}")
        return f"**{data.get('station')}**\n" + "\n".join(lines)


# -- Provider ------------------------------------------------------------


def _fetch_dbf(cfg) -> list[dict[str, Any]]:
    from urllib.parse import quote

    station = cfg.get("TRANSIT_STATION")
    payload = http_get_json(
        DBF_URL.format(station=quote(station)), cfg, params={"version": 3}
    )
    result = []
    for entry in payload.get("departures") or []:
        time_str = entry.get("scheduledDeparture")
        if not time_str:
            continue  # Zug endet hier, keine Weiterfahrt
        result.append(
            {
                "line": _clean(entry.get("train")),
                "destination": _clean(entry.get("destination")),
                "time": time_str,
                "delay_min": entry.get("delayDeparture") or 0,
                "platform": _clean(entry.get("platform")),
            }
        )
    return result


def _fetch_hafas(cfg) -> list[dict[str, Any]]:
    base = cfg.get("TRANSIT_API_BASE", "https://v6.db.transport.rest").rstrip("/")
    stop_id = cfg.get("TRANSIT_STOP_ID")
    payload = http_get_json(
        f"{base}/stops/{stop_id}/departures",
        cfg,
        params={
            "duration": cfg.get_int("TRANSIT_WINDOW_MIN", 60),
            "results": max(20, cfg.get_int("TRANSIT_MAX", 5) * 4),
        },
    )
    entries = payload.get("departures") if isinstance(payload, dict) else payload
    result = []
    for entry in entries or []:
        when = entry.get("when") or entry.get("plannedWhen")
        if not when:
            continue  # ausgefallene Fahrt
        result.append(
            {
                "line": _clean((entry.get("line") or {}).get("name")),
                "destination": _clean((entry.get("destination") or {}).get("name")),
                "time": when[11:16],
                "delay_min": round((entry.get("delay") or 0) / 60),
                "platform": _clean(entry.get("platform")),
            }
        )
    return result


def search_stops(query: str, cfg) -> list[dict[str, Any]]:
    """Haltestellensuche für `--transit-search` (nur transport.rest-Instanzen)."""
    base = cfg.get("TRANSIT_API_BASE", "https://v6.db.transport.rest").rstrip("/")
    payload = http_get_json(
        f"{base}/locations",
        cfg,
        params={"query": query, "results": 8, "poi": "false", "addresses": "false"},
    )
    return [
        {"id": item.get("id"), "name": item.get("name")}
        for item in (payload or [])
        if item.get("id")
    ]


# -- Hilfsfunktionen -----------------------------------------------------


def _provider(cfg) -> str:
    return cfg.get("TRANSIT_PROVIDER", "dbf").lower()


def _filter(cfg, departures: list[dict]) -> list[dict]:
    """Richtungs-/Linienfilter und Zeitfenster anwenden."""
    destinations = [d.lower() for d in cfg.get_list("TRANSIT_FILTER_DEST")]
    lines = [l.lower() for l in cfg.get_list("TRANSIT_FILTER_LINE")]
    window = cfg.get_int("TRANSIT_WINDOW_MIN", 60)
    now = dt.datetime.now()

    result = []
    for dep in departures:
        if destinations and not any(d in dep["destination"].lower() for d in destinations):
            continue
        if lines and not any(l in dep["line"].lower() for l in lines):
            continue
        minutes = _minutes_from_now(dep["time"], now)
        if minutes is None or minutes < 0 or minutes > window:
            continue
        dep["in_minutes"] = minutes
        result.append(dep)

    result.sort(key=lambda d: d["in_minutes"])
    return result


def _minutes_from_now(hhmm: str, now: dt.datetime) -> int | None:
    try:
        hours, minutes = (int(part) for part in hhmm.split(":")[:2])
    except (ValueError, TypeError):
        return None
    departure = now.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    # Abfahrten kurz nach Mitternacht gehören zum nächsten Tag.
    if departure < now - dt.timedelta(hours=2):
        departure += dt.timedelta(days=1)
    return round((departure - now).total_seconds() / 60)


def _delay_suffix(dep: dict) -> str:
    delay = dep.get("delay_min") or 0
    return f"+{delay}" if delay > 0 else ""


def _clean(value) -> str:
    return " ".join(str(value).split()) if value else ""


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
