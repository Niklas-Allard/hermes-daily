"""Konfiguration aus der .env — ein Config-Objekt wird durch alle Module gereicht."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

VERBOSITY_LEVELS = ("compact", "normal", "full")

# Reihenfolge, in der die Abschnitte in der Ausgabe erscheinen.
MODULE_ORDER = (
    "untis",
    "calendar",
    "tasks",
    "transit",
    "weather",
    "advice",
    "news",
)

TRUTHY = {"1", "true", "yes", "on", "ja", "y"}
FALSY = {"0", "false", "no", "off", "nein", "n", ""}


class Config:
    """Dünner Wrapper um os.environ mit Typkonvertierung und Defaults."""

    def __init__(self, env: dict[str, str] | None = None):
        self.env = env if env is not None else dict(os.environ)

    # -- Zugriff ---------------------------------------------------------

    def get(self, key: str, default: str = "") -> str:
        value = self.env.get(key)
        return default if value is None or value.strip() == "" else value.strip()

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw = self.env.get(key)
        if raw is None or raw.strip() == "":
            return default
        raw = raw.strip().lower()
        if raw in TRUTHY:
            return True
        if raw in FALSY:
            return False
        return default

    def get_int(self, key: str, default: int) -> int:
        try:
            return int(self.get(key, str(default)))
        except ValueError:
            return default

    def get_float(self, key: str, default: float | None = None) -> float | None:
        raw = self.get(key)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def get_list(self, key: str, default: list[str] | None = None) -> list[str]:
        """Kommagetrennte Liste; leere Einträge werden verworfen."""
        raw = self.get(key)
        if not raw:
            return list(default or [])
        return [part.strip() for part in raw.split(",") if part.strip()]

    def get_mapping(self, key: str) -> dict[str, str]:
        """`a=1,b=2` -> {"a": "1", "b": "2"}."""
        result: dict[str, str] = {}
        for item in self.get_list(key):
            if "=" not in item:
                continue
            name, _, value = item.partition("=")
            name, value = name.strip(), value.strip()
            if name and value:
                result[name] = value
        return result

    def get_path(self, key: str, default: str = "") -> Path | None:
        raw = self.get(key, default)
        if not raw:
            return None
        return Path(raw).expanduser()

    # -- Abgeleitete Einstellungen ---------------------------------------

    def is_enabled(self, module_name: str) -> bool:
        return self.get_bool(f"ENABLE_{module_name.upper()}", True)

    @property
    def verbosity(self) -> str:
        value = self.get("HERMES_VERBOSITY", "compact").lower()
        return value if value in VERBOSITY_LEVELS else "compact"

    @property
    def timeout(self) -> int:
        return max(1, self.get_int("HERMES_TIMEOUT", 12))

    @property
    def max_chars(self) -> int:
        return max(0, self.get_int("HERMES_MAX_CHARS", 0))

    @property
    def timezone(self) -> str:
        return self.get("WEATHER_TZ", "Europe/Berlin")

    @property
    def history_dir(self) -> Path:
        return ROOT / "history"


def load_config(env_file: Path | None = None) -> Config:
    """Lädt die .env (ohne bereits gesetzte Umgebungsvariablen zu überschreiben)."""
    path = env_file or (ROOT / ".env")
    if path.is_file():
        load_dotenv(path, override=False)
    return Config()
