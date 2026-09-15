"""Schlagzeilen aus konfigurierbaren RSS-/Atom-Feeds."""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

import feedparser
import requests

from .base import Module, USER_AGENT


class NewsModule(Module):
    name = "news"
    title = "Nachrichten"

    def configured(self, cfg) -> tuple[bool, str]:
        if not cfg.get_list("NEWS_FEEDS"):
            return False, "NEWS_FEEDS ist leer"
        return True, ""

    def fetch(self, cfg, ctx) -> dict[str, Any]:
        max_per_feed = cfg.get_int("NEWS_MAX_PER_FEED", 3)
        max_age = cfg.get_int("NEWS_MAX_AGE_HOURS", 24)
        cutoff = time.time() - max_age * 3600 if max_age > 0 else None

        items: list[dict[str, Any]] = []
        errors: list[str] = []

        for url in cfg.get_list("NEWS_FEEDS"):
            try:
                # Bewusst über requests statt feedparser(url): so gilt unser Timeout.
                response = requests.get(
                    url, headers={"User-Agent": USER_AGENT}, timeout=cfg.timeout
                )
                response.raise_for_status()
                feed = feedparser.parse(response.content)
            except Exception as exc:  # noqa: BLE001 - ein toter Feed darf nicht blockieren
                errors.append(f"{_host(url)}: {type(exc).__name__}")
                continue

            source = _clean(feed.feed.get("title")) or _host(url)
            taken = 0
            for entry in feed.entries:
                if taken >= max_per_feed:
                    break
                published = _timestamp(entry)
                if cutoff and published and published < cutoff:
                    continue
                items.append(
                    {
                        "source": source,
                        "title": _clean(entry.get("title")),
                        "link": entry.get("link", ""),
                        "summary": _clean(entry.get("summary")),
                        "published": dt.datetime.fromtimestamp(published).isoformat(
                            timespec="minutes"
                        )
                        if published
                        else None,
                    }
                )
                taken += 1

        return {"items": items, "errors": errors}

    def render(self, data: dict[str, Any], verbosity: str) -> str:
        items = data.get("items") or []
        if not items:
            return ""

        if verbosity == "compact":
            return "News: " + " | ".join(item["title"] for item in items)

        lines = []
        for item in items:
            line = f"**{item['source']}**: {item['title']}"
            if verbosity == "full":
                if item.get("summary"):
                    line += f"\n  {_truncate(item['summary'], 220)}"
                if item.get("link"):
                    line += f"\n  {item['link']}"
            lines.append(f"- {line}")
        for error in data.get("errors") or []:
            lines.append(f"- ⚠️ {error}")
        return "\n".join(lines)


def _timestamp(entry) -> float | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return time.mktime(parsed)
    return None


def _clean(value) -> str:
    if not value:
        return ""
    import re

    text = re.sub(r"<[^>]+>", "", str(value))
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc or url[:30]
