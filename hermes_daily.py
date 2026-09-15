#!/usr/bin/env python3
"""hermes-daily — ein Aufruf, ein Tagescheckup.

Sammelt Wetter, Stundenplan, Termine, Aufgaben, Abfahrten und Nachrichten ein
und gibt sie als Markdown (Default) oder JSON aus.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from hermes import history
from hermes.config import VERBOSITY_LEVELS, load_config
from hermes.render import render_json, render_markdown
from hermes.runner import MODULES_BY_NAME, run, select_modules


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-daily",
        description="Morgendlicher Tagescheckup für den Hermes-Agenten.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Beispiele:\n"
            "  hermes-daily                        Checkup für heute (compact)\n"
            "  hermes-daily --full                 ausführliche Fassung\n"
            "  hermes-daily --format json          Maschinenformat, immer vollständig\n"
            "  hermes-daily --week                 Wochenübersicht\n"
            '  hermes-daily --geocode "Stuttgart"  Koordinaten für die .env finden\n'
        ),
    )
    parser.add_argument("--date", help="Zieltag als YYYY-MM-DD (Default: heute)")
    parser.add_argument(
        "--week",
        action="store_true",
        help="Wochenübersicht ab dem Zieltag statt nur einem Tag",
    )
    parser.add_argument(
        "--format", choices=("md", "json"), default="md", help="Ausgabeformat (Default: md)"
    )
    parser.add_argument(
        "--verbosity",
        choices=VERBOSITY_LEVELS,
        help="Ausführlichkeit (Default aus HERMES_VERBOSITY, sonst compact)",
    )
    parser.add_argument(
        "--compact", action="store_true", help="Kurzform — spart Tokens (wie --verbosity compact)"
    )
    parser.add_argument(
        "--full", action="store_true", help="Alles anzeigen (wie --verbosity full)"
    )
    parser.add_argument("--only", help="Nur diese Module, kommagetrennt (hebt .env-Flags auf)")
    parser.add_argument("--skip", help="Diese Module auslassen, kommagetrennt")
    parser.add_argument(
        "--no-history", action="store_true", help="Diesen Lauf nicht in history/ ablegen"
    )

    helpers = parser.add_argument_group("Helfer")
    helpers.add_argument("--check", action="store_true", help="Konfiguration prüfen, nichts abrufen")
    helpers.add_argument("--geocode", metavar="ORT", help="Koordinaten für WEATHER_LAT/LON suchen")
    helpers.add_argument(
        "--untis-discover", metavar="SCHULE", help="UNTIS_SERVER und UNTIS_SCHOOL suchen"
    )
    helpers.add_argument(
        "--transit-search", metavar="HALT", help="TRANSIT_STOP_ID suchen (nur transport.rest)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()

    if args.geocode:
        return cmd_geocode(cfg, args.geocode)
    if args.untis_discover:
        return cmd_untis_discover(cfg, args.untis_discover)
    if args.transit_search:
        return cmd_transit_search(cfg, args.transit_search)
    if args.check:
        return cmd_check(cfg)

    try:
        target_date = (
            dt.date.fromisoformat(args.date) if args.date else dt.date.today()
        )
    except ValueError:
        print(f"Ungültiges Datum: {args.date} (erwartet YYYY-MM-DD)", file=sys.stderr)
        return 1

    verbosity = args.verbosity or ("full" if args.full else "compact" if args.compact else cfg.verbosity)

    only = _split(args.only)
    skip = _split(args.skip)
    if unknown := [n for n in (only + skip) if n not in MODULES_BY_NAME]:
        print(
            f"Unbekannte Module: {', '.join(unknown)}. "
            f"Verfügbar: {', '.join(sorted(MODULES_BY_NAME))}",
            file=sys.stderr,
        )
        return 1

    modules = select_modules(cfg, only, skip)
    if not modules:
        print("Kein Modul aktiv — prüfe die .env oder nutze --only.", file=sys.stderr)
        return 1

    extra = {}
    if args.week:
        # Ab dem Zieltag bis zum Ende der Schulwoche (Freitag), mindestens 4 Tage.
        days_to_friday = max(4, 4 - target_date.weekday())
        extra["range_end"] = target_date + dt.timedelta(days=days_to_friday)

    result = run(cfg, modules, target_date, extra)

    use_history = cfg.get_bool("ENABLE_HISTORY", True) and not args.no_history and not args.week
    if use_history:
        previous = history.load_snapshot(cfg, target_date)
        diff = history.diff_timetable(previous, result)
        if diff:
            result["diff"] = diff
        history.save_snapshot(cfg, result, target_date)
        history.prune(cfg, cfg.get_int("HISTORY_KEEP_DAYS", 60))

    if args.format == "json":
        print(render_json(result))
    else:
        print(render_markdown(result, verbosity, cfg.max_chars), end="")
    return 0


# -- Helfer-Befehle ------------------------------------------------------


def cmd_geocode(cfg, query: str) -> int:
    from hermes.modules.weather import geocode

    try:
        results = geocode(query, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"Ortssuche fehlgeschlagen: {exc}", file=sys.stderr)
        return 1

    if not results:
        print(f"Kein Ort gefunden für: {query}", file=sys.stderr)
        return 1

    print(f"Treffer für {query!r} — Werte in die .env übernehmen:\n")
    for place in results:
        region = ", ".join(
            part for part in (place.get("admin1"), place.get("country")) if part
        )
        print(f"  {place['name']} ({region})")
        print(f"    WEATHER_LAT={place['latitude']:.4f}")
        print(f"    WEATHER_LON={place['longitude']:.4f}")
        print(f"    WEATHER_LOCATION_NAME={place['name']}")
        print(f"    WEATHER_TZ={place.get('timezone', 'Europe/Berlin')}\n")
    return 0


def cmd_untis_discover(cfg, query: str) -> int:
    from hermes.modules.untis import search_schools

    try:
        schools = search_schools(query, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"Schulsuche fehlgeschlagen: {exc}", file=sys.stderr)
        return 1

    if not schools:
        print(f"Keine Schule gefunden für: {query}", file=sys.stderr)
        return 1

    print(f"Treffer für {query!r} — Werte in die .env übernehmen:\n")
    for school in schools:
        print(f"  {school.get('displayName')} — {school.get('address', '')}")
        print(f"    UNTIS_SERVER={school.get('server')}")
        print(f"    UNTIS_SCHOOL={school.get('loginName')}\n")
    return 0


def cmd_transit_search(cfg, query: str) -> int:
    from hermes.modules.transit import search_stops

    try:
        stops = search_stops(query, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"Haltestellensuche fehlgeschlagen: {exc}", file=sys.stderr)
        print(
            "Hinweis: die Suche nutzt TRANSIT_API_BASE. Die DB-Instanz "
            "(v6.db.transport.rest) ist derzeit abgeschaltet — für TRANSIT_PROVIDER=dbf "
            "brauchst du keine Stop-ID, sondern nur TRANSIT_STATION.",
            file=sys.stderr,
        )
        return 1

    if not stops:
        print(f"Keine Haltestelle gefunden für: {query}", file=sys.stderr)
        return 1

    print(f"Treffer für {query!r} — Wert in die .env übernehmen:\n")
    for stop in stops:
        print(f"  {stop['name']}")
        print(f"    TRANSIT_STOP_ID={stop['id']}\n")
    return 0


def cmd_check(cfg) -> int:
    from hermes.config import MODULE_ORDER

    print(f"Verbosity: {cfg.verbosity} · Timeout: {cfg.timeout}s · Zeitzone: {cfg.timezone}")
    print(f"Verlauf: {'an' if cfg.get_bool('ENABLE_HISTORY', True) else 'aus'} ({cfg.history_dir})")
    print("\nModule:")

    active = 0
    for name in MODULE_ORDER:
        module = MODULES_BY_NAME[name]
        if not module.enabled(cfg):
            print(f"  [ ] {name:<9} abgeschaltet (ENABLE_{name.upper()}=false)")
            continue
        ok, hint = module.configured(cfg)
        if ok:
            active += 1
            print(f"  [x] {name:<9} bereit")
        else:
            print(f"  [!] {name:<9} {hint}")

    print(f"\n{active} von {len(MODULE_ORDER)} Modulen einsatzbereit.")
    if active == 0:
        print("Trage die fehlenden Werte in die .env ein (Vorlage: .env.example).")
    return 0


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


if __name__ == "__main__":
    sys.exit(main())
