"""Verlaufs-Log und Stundenplan-Diff.

Pro Zieltag wird der jeweils letzte Lauf als JSON abgelegt. Beim nächsten Lauf
für denselben Tag wird gegen diesen Snapshot verglichen — so fällt eine
kurzfristige Vertretung sofort auf, statt in der Gesamtausgabe unterzugehen.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


def _snapshot_path(cfg, target_date: dt.date) -> Path:
    return cfg.history_dir / f"{target_date.isoformat()}.json"


def load_snapshot(cfg, target_date: dt.date) -> dict[str, Any] | None:
    path = _snapshot_path(cfg, target_date)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_snapshot(cfg, result: dict[str, Any], target_date: dt.date) -> None:
    cfg.history_dir.mkdir(parents=True, exist_ok=True)
    path = _snapshot_path(cfg, target_date)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def prune(cfg, keep_days: int) -> None:
    if keep_days <= 0 or not cfg.history_dir.is_dir():
        return
    cutoff = dt.date.today() - dt.timedelta(days=keep_days)
    for path in cfg.history_dir.glob("*.json"):
        try:
            file_date = dt.date.fromisoformat(path.stem)
        except ValueError:
            continue
        if file_date < cutoff:
            path.unlink(missing_ok=True)


def diff_timetable(previous: dict | None, current: dict) -> dict[str, Any]:
    """Vergleicht die Stundenpläne zweier Läufe für denselben Tag."""
    if not previous:
        return {}

    # Ohne belastbare Daten auf beiden Seiten würde ein fehlgeschlagener Abruf
    # den kompletten Stundenplan als "ENTFERNT" melden — schlimmer als kein Diff.
    if not _has_timetable(previous) or not _has_timetable(current):
        return {}

    old_periods = _periods(previous)
    new_periods = _periods(current)
    if not old_periods and not new_periods:
        return {}

    lines: list[str] = []

    for period_id, new in new_periods.items():
        old = old_periods.get(period_id)
        if old is None:
            lines.append(f"NEU: {_describe(new)}")
            continue
        if old.get("status") != new.get("status"):
            lines.append(
                f"GEÄNDERT: {_label(new)} {_status_text(old)} → {_status_text(new)}"
            )
        elif old.get("room") != new.get("room"):
            lines.append(
                f"RAUM: {_label(new)} {old.get('room') or '?'} → {new.get('room') or '?'}"
            )
        elif old.get("subject") != new.get("subject"):
            lines.append(
                f"FACH: {_label(new)} {old.get('subject')} → {new.get('subject')}"
            )

    for period_id, old in old_periods.items():
        if period_id not in new_periods:
            lines.append(f"ENTFERNT: {_describe(old)}")

    return {"lines": lines} if lines else {}


def _has_timetable(result: dict) -> bool:
    """True, wenn der Lauf einen echten Untis-Abruf enthält (kein Fehler/Skip)."""
    untis = (result.get("modules") or {}).get("untis")
    if not isinstance(untis, dict):
        return False
    return "error" not in untis and "skipped" not in untis and "periods" in untis


def _periods(result: dict) -> dict[str, dict]:
    untis = (result.get("modules") or {}).get("untis") or {}
    periods = untis.get("periods") or []
    return {str(p.get("id")): p for p in periods if p.get("id") is not None}


def _label(period: dict) -> str:
    start = period.get("start") or "?"
    return f"{start} {period.get('subject') or '?'}"


def _status_text(period: dict) -> str:
    return {
        "cancelled": "entfällt",
        "irregular": "Vertretung",
        "regular": "regulär",
    }.get(period.get("status") or "regular", period.get("status") or "regulär")


def _describe(period: dict) -> str:
    text = _label(period)
    if period.get("room"):
        text += f" ({period['room']})"
    if period.get("status") and period["status"] != "regular":
        text += f" — {_status_text(period)}"
    return text
