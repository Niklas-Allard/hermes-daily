"""Offene Aufgaben aus einer lokalen Markdown-Checkliste.

Erkennt `- [ ] Aufgabe` und ein optionales Fälligkeitsdatum `@2026-09-10`.
Untis-Hausaufgaben bleiben bewusst im Untis-Modul, damit beide Quellen
unabhängig voneinander ausfallen können.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .base import Module

TASK_PATTERN = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$")
DUE_PATTERN = re.compile(r"@(\d{4}-\d{2}-\d{2})")


class TasksModule(Module):
    name = "tasks"
    title = "Aufgaben"

    def configured(self, cfg) -> tuple[bool, str]:
        path = cfg.get_path("TASKS_FILE")
        if path is None:
            return False, "TASKS_FILE ist nicht gesetzt"
        if not path.is_file():
            return False, f"TASKS_FILE nicht gefunden: {path}"
        return True, ""

    def fetch(self, cfg, ctx) -> dict[str, Any]:
        path = cfg.get_path("TASKS_FILE")
        target_date: dt.date = ctx["target_date"]
        horizon = cfg.get_int("TASKS_HORIZON_DAYS", 7)
        limit = cfg.get_int("TASKS_MAX", 10)

        open_tasks: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = TASK_PATTERN.match(line)
            if not match or match.group(1).lower() == "x":
                continue

            text = match.group(2)
            due_match = DUE_PATTERN.search(text)
            due = None
            if due_match:
                text = DUE_PATTERN.sub("", text).strip()
                try:
                    due = dt.date.fromisoformat(due_match.group(1))
                except ValueError:
                    due = None

            open_tasks.append(
                {
                    "text": text,
                    "due": due.isoformat() if due else None,
                    "days_until": (due - target_date).days if due else None,
                    "overdue": bool(due and due < target_date),
                }
            )

        # Erst Überfälliges, dann Fälliges nach Datum, dann Undatiertes.
        relevant = [
            task
            for task in open_tasks
            if task["days_until"] is None or task["days_until"] <= horizon
        ]
        relevant.sort(key=lambda t: (t["days_until"] is None, t["days_until"] or 0))

        return {
            "tasks": relevant[:limit],
            "total_open": len(open_tasks),
            "hidden": max(0, len(relevant) - limit),
        }

    def render(self, data: dict[str, Any], verbosity: str) -> str:
        tasks = data.get("tasks") or []
        if not tasks:
            return ""

        if verbosity == "compact":
            parts = []
            for task in tasks:
                label = task["text"]
                if task["overdue"]:
                    label = "!" + label
                elif task["days_until"] == 0:
                    label += " (heute)"
                parts.append(label)
            return "Todo: " + " | ".join(parts)

        lines = []
        for task in tasks:
            suffix = ""
            if task["overdue"]:
                suffix = f" — **überfällig** (seit {task['due']})"
            elif task["days_until"] == 0:
                suffix = " — **heute fällig**"
            elif task["days_until"] is not None:
                suffix = f" — in {task['days_until']} Tagen"
            lines.append(f"- {task['text']}{suffix}")
        if data.get("hidden"):
            lines.append(f"- _… und {data['hidden']} weitere_")
        return "\n".join(lines)
