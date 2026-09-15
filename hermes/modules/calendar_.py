"""Termine aus ICS-Feeds und/oder CalDAV-Servern.

Beide Quellen sind optional und werden zu einer nach Startzeit sortierten
Terminliste zusammengeführt. Wiederholungstermine löst bei ICS
`recurring_ical_events` auf, bei CalDAV übernimmt das der Server (`expand=True`).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import requests

from .base import Module, USER_AGENT


class CalendarModule(Module):
    name = "calendar"
    title = "Termine"

    def configured(self, cfg) -> tuple[bool, str]:
        if cfg.get_list("CALENDAR_ICS_URLS") or cfg.get("CALDAV_URL"):
            return True, ""
        return False, "weder CALENDAR_ICS_URLS noch CALDAV_URL gesetzt"

    def fetch(self, cfg, ctx) -> dict[str, Any]:
        start: dt.date = ctx["target_date"]
        end: dt.date = (ctx.get("range_end") or start) + dt.timedelta(days=1)

        events: list[dict[str, Any]] = []
        errors: list[str] = []

        for url in cfg.get_list("CALENDAR_ICS_URLS"):
            try:
                events.extend(self._fetch_ics(cfg, url, start, end))
            except Exception as exc:  # noqa: BLE001 - eine kaputte URL darf den Rest nicht kippen
                errors.append(f"ICS {_host(url)}: {type(exc).__name__}")

        if cfg.get("CALDAV_URL"):
            try:
                events.extend(self._fetch_caldav(cfg, start, end))
            except Exception as exc:  # noqa: BLE001
                # CalDAV-Fehler transportieren gern die komplette HTML-Fehlerseite.
                errors.append(f"CalDAV: {type(exc).__name__}: {_truncate(_flat(exc), 120)}")

        events = _dedupe(events)
        events.sort(key=lambda e: (not e["all_day"], e["start"] or ""))
        return {"events": events, "errors": errors}

    # -- Quellen ---------------------------------------------------------

    def _fetch_ics(self, cfg, url: str, start: dt.date, end: dt.date) -> list[dict]:
        import icalendar
        import recurring_ical_events

        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=cfg.timeout
        )
        response.raise_for_status()
        calendar = icalendar.Calendar.from_ical(response.content)
        occurrences = recurring_ical_events.of(calendar).between(start, end)
        return [_normalize(event, start) for event in occurrences]

    def _fetch_caldav(self, cfg, start: dt.date, end: dt.date) -> list[dict]:
        import caldav

        client = caldav.DAVClient(
            url=cfg.get("CALDAV_URL"),
            username=cfg.get("CALDAV_USERNAME") or None,
            password=cfg.get("CALDAV_PASSWORD") or None,
            timeout=cfg.timeout,
        )
        wanted = {name.lower() for name in cfg.get_list("CALDAV_CALENDARS")}

        events: list[dict] = []
        for calendar in client.principal().calendars():
            name = str(getattr(calendar, "name", "") or "")
            if wanted and name.lower() not in wanted:
                continue
            found = calendar.search(
                start=dt.datetime.combine(start, dt.time.min),
                end=dt.datetime.combine(end, dt.time.min),
                event=True,
                expand=True,
            )
            for item in found:
                for component in item.icalendar_instance.walk("VEVENT"):
                    normalized = _normalize(component, start)
                    normalized["calendar"] = name
                    events.append(normalized)
        return events

    # -- Darstellung -----------------------------------------------------

    def render(self, data: dict[str, Any], verbosity: str) -> str:
        events = data.get("events") or []
        errors = data.get("errors") or []

        if not events:
            if not errors:
                return ""
            if verbosity == "compact":
                return "Termine: Fehler — " + "; ".join(errors)
            return "\n".join(f"- ⚠️ {error}" for error in errors)

        if verbosity == "compact":
            parts = []
            for event in events:
                prefix = "ganztägig" if event["all_day"] else _time_of(event["start"])
                parts.append(f"{prefix} {event['summary']}")
            line = "Termine: " + " | ".join(parts)
            return line

        lines = []
        for event in events:
            when = "ganztägig" if event["all_day"] else _time_range(event)
            text = f"{when} — {event['summary']}"
            if event.get("location"):
                text += f" @ {event['location']}"
            if verbosity == "full" and event.get("description"):
                text += f"\n  {_truncate(event['description'], 200)}"
            lines.append(f"- {text}")
        for error in errors:
            lines.append(f"- ⚠️ {error}")
        return "\n".join(lines)


def _normalize(component, target_date: dt.date) -> dict[str, Any]:
    start = component.get("DTSTART")
    end = component.get("DTEND")
    start_value = start.dt if start is not None else None
    end_value = end.dt if end is not None else None

    all_day = isinstance(start_value, dt.date) and not isinstance(start_value, dt.datetime)

    return {
        "summary": _text(component.get("SUMMARY")) or "(ohne Titel)",
        "location": _text(component.get("LOCATION")),
        "description": _text(component.get("DESCRIPTION")),
        "start": _iso(start_value),
        "end": _iso(end_value),
        "all_day": all_day,
    }


def _dedupe(events: list[dict]) -> list[dict]:
    """Gleicher Titel zur gleichen Zeit aus mehreren Quellen -> einmal zeigen."""
    seen = set()
    unique = []
    for event in events:
        key = (event["summary"], event["start"], event["all_day"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


def _text(value) -> str:
    return " ".join(str(value).split()) if value else ""


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _time_of(iso: str | None) -> str:
    return iso[11:16] if iso and len(iso) >= 16 else "?"


def _time_range(event: dict) -> str:
    start = _time_of(event["start"])
    end = _time_of(event["end"])
    return f"{start}–{end}" if end != "?" else start


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _flat(value) -> str:
    """Mehrzeilige Fehlertexte auf eine Zeile zusammenziehen."""
    return " ".join(str(value).split())


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc or url[:30]
