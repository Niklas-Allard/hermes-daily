"""Modul-Protokoll und gemeinsame Helfer für alle Datenquellen."""

from __future__ import annotations

from typing import Any

import requests

USER_AGENT = "hermes-daily/1.0 (+https://github.com/)"


class Module:
    """Basisklasse für alle Checkup-Module.

    Ein Modul trennt strikt zwischen Beschaffung (`fetch`) und Darstellung
    (`render`). Nur so kann `--format json` die vollen Daten ausgeben, während
    der Markdown-Renderer je nach Verbosity kürzt.
    """

    name: str = ""
    title: str = ""
    #: Module mit ``needs_context`` laufen erst, wenn alle Netz-Module fertig sind.
    needs_context: bool = False

    def enabled(self, cfg) -> bool:
        return cfg.is_enabled(self.name)

    def configured(self, cfg) -> tuple[bool, str]:
        """(ist_konfiguriert, Hinweis-bei-fehlender-Konfiguration)."""
        return True, ""

    def fetch(self, cfg, ctx: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def render(self, data: dict[str, Any], verbosity: str) -> str:
        raise NotImplementedError


def http_get_json(url: str, cfg, *, params: dict | None = None, **kwargs) -> Any:
    """GET mit Timeout und User-Agent — die Variante, die alle Module nutzen."""
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    response = requests.get(
        url, params=params, headers=headers, timeout=cfg.timeout, **kwargs
    )
    response.raise_for_status()
    return response.json()


def http_post_json(url: str, cfg, *, json: dict, **kwargs) -> Any:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    response = requests.post(
        url, json=json, headers=headers, timeout=cfg.timeout, **kwargs
    )
    response.raise_for_status()
    return response.json()


def bullet(lines: list[str], prefix: str = "- ") -> str:
    return "\n".join(f"{prefix}{line}" for line in lines)
