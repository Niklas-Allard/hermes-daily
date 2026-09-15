"""Wetter, UV und Pollen über Open-Meteo (kein API-Key nötig)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .base import Module, http_get_json

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

DAILY_VARS = (
    "weather_code,temperature_2m_min,temperature_2m_max,"
    "precipitation_probability_max,precipitation_sum,sunrise,sunset,"
    "wind_speed_10m_max,uv_index_max"
)
HOURLY_VARS = "temperature_2m,precipitation_probability,weather_code"

POLLEN_VARS = (
    "grass_pollen",
    "birch_pollen",
    "alder_pollen",
    "ragweed_pollen",
    "mugwort_pollen",
    "olive_pollen",
)

# WMO-Wettercodes -> deutsche Kurzbeschreibung
WMO_CODES = {
    0: "klar",
    1: "überwiegend klar",
    2: "teils bewölkt",
    3: "bedeckt",
    45: "Nebel",
    48: "Reifnebel",
    51: "leichter Nieselregen",
    53: "Nieselregen",
    55: "starker Nieselregen",
    56: "gefrierender Nieselregen",
    57: "starker gefrierender Nieselregen",
    61: "leichter Regen",
    63: "Regen",
    65: "starker Regen",
    66: "gefrierender Regen",
    67: "starker gefrierender Regen",
    71: "leichter Schneefall",
    73: "Schneefall",
    75: "starker Schneefall",
    77: "Schneegriesel",
    80: "leichte Schauer",
    81: "Schauer",
    82: "heftige Schauer",
    85: "leichte Schneeschauer",
    86: "starke Schneeschauer",
    95: "Gewitter",
    96: "Gewitter mit Hagel",
    99: "schweres Gewitter mit Hagel",
}

# Schwellen in grains/m³ (grob nach DWD-Pollenflugindex)
POLLEN_THRESHOLDS = ((1, "gering"), (10, "mittel"), (50, "hoch"))

POLLEN_NAMES = {
    "grass_pollen": "Gräser",
    "birch_pollen": "Birke",
    "alder_pollen": "Erle",
    "ragweed_pollen": "Ambrosia",
    "mugwort_pollen": "Beifuß",
    "olive_pollen": "Olive",
}


def geocode(name: str, cfg) -> list[dict[str, Any]]:
    """Ortssuche für den `--geocode`-Helfer."""
    payload = http_get_json(
        GEOCODING_URL,
        cfg,
        params={"name": name, "count": 5, "language": "de", "format": "json"},
    )
    return payload.get("results") or []


def describe_code(code: int | None) -> str:
    if code is None:
        return "unbekannt"
    return WMO_CODES.get(int(code), f"Code {code}")


def pollen_level(value: float | None) -> str | None:
    if value is None or value < 1:
        return None
    level = "gering"
    for threshold, label in POLLEN_THRESHOLDS:
        if value >= threshold:
            level = label
    return level


class WeatherModule(Module):
    name = "weather"
    title = "Wetter"

    def configured(self, cfg) -> tuple[bool, str]:
        if cfg.get_float("WEATHER_LAT") is None or cfg.get_float("WEATHER_LON") is None:
            return False, "WEATHER_LAT/WEATHER_LON fehlen (ermitteln mit --geocode \"Stadt\")"
        return True, ""

    def fetch(self, cfg, ctx) -> dict[str, Any]:
        target_date: dt.date = ctx["target_date"]
        lat = cfg.get_float("WEATHER_LAT")
        lon = cfg.get_float("WEATHER_LON")

        payload = http_get_json(
            FORECAST_URL,
            cfg,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": DAILY_VARS,
                "hourly": HOURLY_VARS,
                "timezone": cfg.timezone,
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
            },
        )

        daily = payload.get("daily") or {}
        data: dict[str, Any] = {
            "location": cfg.get("WEATHER_LOCATION_NAME", "") or f"{lat:.2f},{lon:.2f}",
            "code": _first(daily.get("weather_code")),
            "description": describe_code(_first(daily.get("weather_code"))),
            "temp_min": _round(_first(daily.get("temperature_2m_min"))),
            "temp_max": _round(_first(daily.get("temperature_2m_max"))),
            "precip_prob": _round(_first(daily.get("precipitation_probability_max"))),
            "precip_sum": _round(_first(daily.get("precipitation_sum")), 1),
            "wind_max": _round(_first(daily.get("wind_speed_10m_max"))),
            "uv_max": _round(_first(daily.get("uv_index_max")), 1),
            "sunrise": _time_part(_first(daily.get("sunrise"))),
            "sunset": _time_part(_first(daily.get("sunset"))),
        }

        data["rain_from"] = self._first_rainy_hour(cfg, payload)

        if cfg.get_bool("ENABLE_AIR_QUALITY", False):
            data["air"] = self._fetch_air_quality(cfg, lat, lon)

        return data

    def _first_rainy_hour(self, cfg, payload: dict) -> str | None:
        """Erste Stunde im Tagesfenster mit nennenswerter Regenwahrscheinlichkeit."""
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        probs = hourly.get("precipitation_probability") or []
        day_start = cfg.get_int("WEATHER_DAY_START", 7)
        day_end = cfg.get_int("WEATHER_DAY_END", 18)

        for time_str, prob in zip(times, probs):
            if prob is None or prob < 50:
                continue
            hour = int(time_str[11:13])
            if day_start <= hour <= day_end:
                return time_str[11:16]
        return None

    def _fetch_air_quality(self, cfg, lat, lon) -> dict[str, Any]:
        try:
            payload = http_get_json(
                AIR_QUALITY_URL,
                cfg,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "european_aqi,pm2_5," + ",".join(POLLEN_VARS),
                    "timezone": cfg.timezone,
                },
            )
        except Exception as exc:  # noqa: BLE001 - Luftqualität ist optional
            return {"error": f"{type(exc).__name__}: {exc}"}

        current = payload.get("current") or {}
        pollen = {}
        for key in POLLEN_VARS:
            level = pollen_level(current.get(key))
            if level:
                pollen[POLLEN_NAMES[key]] = level
        return {
            "aqi": _round(current.get("european_aqi")),
            "pm2_5": _round(current.get("pm2_5"), 1),
            "pollen": pollen,
        }

    def render(self, data: dict[str, Any], verbosity: str) -> str:
        temp = f"{data['temp_min']}→{data['temp_max']}°C"
        rain = f"{data['precip_prob']}%"

        if verbosity == "compact":
            parts = [temp, f"{rain}R", data["description"]]
            if data.get("rain_from"):
                parts.append(f"Regen ab {data['rain_from']}")
            line = f"Wetter {data['location']}: " + ", ".join(parts)
            pollen_line = self._pollen_compact(data)
            return line + (f"\n{pollen_line}" if pollen_line else "")

        lines = [
            f"{data['location']}: {data['description']}, {temp}, {rain} Regen"
        ]
        if data.get("rain_from"):
            lines.append(f"Regen wahrscheinlich ab {data['rain_from']} Uhr")
        if verbosity == "full":
            lines.append(
                f"Wind bis {data['wind_max']} km/h · UV-Index {data['uv_max']} · "
                f"Sonne {data['sunrise']}–{data['sunset']}"
            )
        else:
            lines.append(f"Sonnenaufgang {data['sunrise']}, Sonnenuntergang {data['sunset']}")

        air = data.get("air") or {}
        if air and not air.get("error"):
            if air.get("pollen"):
                lines.append(
                    "Pollen: "
                    + ", ".join(f"{name} {level}" for name, level in air["pollen"].items())
                )
            if verbosity == "full" and air.get("aqi") is not None:
                lines.append(f"Luftqualität (EAQI): {air['aqi']}, PM2.5 {air['pm2_5']} µg/m³")

        return "\n".join(f"- {line}" for line in lines)

    def _pollen_compact(self, data: dict) -> str:
        air = data.get("air") or {}
        pollen = air.get("pollen") or {}
        relevant = [f"{name} {level}" for name, level in pollen.items() if level != "gering"]
        return "Pollen: " + ", ".join(relevant) if relevant else ""


def _first(values) -> Any:
    if isinstance(values, list) and values:
        return values[0]
    return None


def _round(value, digits: int = 0):
    if value is None:
        return None
    return round(float(value), digits) if digits else int(round(float(value)))


def _time_part(iso: str | None) -> str | None:
    return iso[11:16] if iso and len(iso) >= 16 else None
