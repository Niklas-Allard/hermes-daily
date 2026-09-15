"""WebUntis: Stundenplan, Vertretungen, Freistunden, Klausuren, HA, Nachrichten.

Die Stammdaten-Properties von `python-webuntis` (`period.subjects`, `.rooms`,
`.teachers`) laden im Hintergrund die kompletten Stammdatenlisten nach. Für
Schüler-Accounts scheitert das häufig an fehlenden Rechten, deshalb greift
`_names()` konsequent auf die Rohdaten der Periode zurück.
"""

from __future__ import annotations

import base64
import datetime as dt
import logging
from typing import Any

import requests
import webuntis

from .base import Module, USER_AGENT, http_post_json

SCHOOLSEARCH_URL = "https://schoolsearch.webuntis.com/schoolquery2"

# python-webuntis loggt JSON-RPC-Fehler ("no right for getTeachers()") auf
# ERROR-Level. Für Schüler-Accounts sind fehlende Stammdatenrechte der Normalfall
# und kein Problem — wir fangen das ab und wollen die Ausgabe nicht zumüllen.
logging.getLogger("webuntis").setLevel(logging.CRITICAL)

#: Stammdaten-Endpunkte, die in diesem Prozess bereits an fehlenden Rechten
#: gescheitert sind. Ohne diese Sperre fragt jede einzelne Periode erneut an.
_DENIED_MASTERDATA: set[str] = set()


class _TimeoutSession(requests.Session):
    """requests-Session mit erzwungenem Timeout.

    `python-webuntis` ruft `http_session.post(...)` ohne Timeout auf. Ein
    langsamer oder drosselnder Untis-Server würde den gesamten Checkup sonst
    unbegrenzt blockieren.
    """

    def __init__(self, timeout: int):
        super().__init__()
        self._timeout = timeout

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return super().request(*args, **kwargs)

STATUS_LABELS = {
    "cancelled": "ENTFÄLLT",
    "irregular": "VERTRETUNG",
}

#: Textbausteine im Vertretungstext, die eine Stunde ohne Lehrkraft kennzeichnen.
#: Untis lässt die Stunde dann als `regular` im Plan stehen ("Vtr. ohne Lehrer"),
#: für die Klasse ist sie aber eine Freistunde. Erweiterbar über
#: `UNTIS_FREE_MARKERS`.
FREE_MARKERS = ("ohne lehrer", "ohne lehrkraft")

#: Untertgrenze für eine Lücke im Plan, damit sie als Freistunde zählt. Eine
#: ausgefallene Stunde reißt mindestens ein Unterrichtsslot auf; alles darunter
#: ist die normale Pause zwischen zwei Stunden.
DEFAULT_GAP_MINUTES = 45


def search_schools(query: str, cfg) -> list[dict[str, Any]]:
    """Schulsuche für `--untis-discover`.

    Die API antwortet bei unspezifischen Suchbegriffen mit dem Fehlercode
    -6003 ("too many results") statt mit einer gekürzten Liste.
    """
    payload = http_post_json(
        SCHOOLSEARCH_URL,
        cfg,
        json={
            "id": "hermes-daily",
            "method": "searchSchool",
            "params": [{"search": query}],
            "jsonrpc": "2.0",
        },
    )
    if "error" in payload:
        error = payload["error"]
        if error.get("code") == -6003:
            raise ValueError(
                "Zu viele Treffer — bitte den Suchbegriff eingrenzen "
                "(z. B. vollständiger Schulname statt nur der Stadt)."
            )
        raise ValueError(f"Schulsuche fehlgeschlagen: {error.get('message', error)}")
    return (payload.get("result") or {}).get("schools") or []


class UntisModule(Module):
    name = "untis"
    title = "Stundenplan"

    REQUIRED = ("UNTIS_SERVER", "UNTIS_SCHOOL", "UNTIS_USERNAME", "UNTIS_PASSWORD")

    def configured(self, cfg) -> tuple[bool, str]:
        missing = [key for key in self.REQUIRED if not cfg.get(key)]
        if missing:
            return False, (
                f"{', '.join(missing)} fehlen "
                '(Server/Schule ermitteln mit --untis-discover "Schulname")'
            )
        return True, ""

    def fetch(self, cfg, ctx) -> dict[str, Any]:
        target_date: dt.date = ctx["target_date"]
        end_date: dt.date = ctx.get("range_end") or target_date

        session = webuntis.Session(
            server=cfg.get("UNTIS_SERVER"),
            school=cfg.get("UNTIS_SCHOOL"),
            username=cfg.get("UNTIS_USERNAME"),
            password=cfg.get("UNTIS_PASSWORD"),
            useragent="hermes-daily",
        )
        session.config["_http_session"] = _TimeoutSession(cfg.timeout)

        with session.login() as s:
            data: dict[str, Any] = {"date": target_date.isoformat()}

            holiday = self._holiday_for(s, target_date)
            if holiday:
                data["holiday"] = holiday

            periods = self._periods(s, cfg, target_date, end_date)
            data["periods"] = periods
            data["gaps"] = self._gaps(periods, cfg)
            data["first_lesson"] = next(
                (p["start"] for p in periods if _attended(p)), None
            )
            data["last_lesson"] = next(
                (p["end"] for p in reversed(periods) if _attended(p)), None
            )
            data["exams"] = self._exams(s, cfg, target_date)

            # Die folgenden beiden Quellen sind nicht offiziell dokumentiert und
            # je nach Untis-Version unterschiedlich — best effort. Wenn deine
            # Schule sie nicht anbietet, kosten sie nur Zeit: dann abschalten.
            data["homework"] = (
                self._homework(s, cfg, target_date, end_date)
                if cfg.get_bool("UNTIS_FETCH_HOMEWORK", True)
                else []
            )
            data["messages"] = (
                self._messages(s, cfg, target_date)
                if cfg.get_bool("UNTIS_FETCH_MESSAGES", True)
                else []
            )

        return data

    # -- Stundenplan -----------------------------------------------------

    def _periods(self, s, cfg, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        markers = tuple(m.lower() for m in cfg.get_list("UNTIS_FREE_MARKERS", list(FREE_MARKERS)))
        table = s.my_timetable(start=start, end=end)
        periods = []
        for period in table:
            status = period.code or "regular"
            entry = {
                "id": period.id,
                "date": period.start.date().isoformat(),
                "start": period.start.strftime("%H:%M"),
                "end": period.end.strftime("%H:%M"),
                "subject": _names(period, "su", "subjects") or "?",
                "room": _names(period, "ro", "rooms"),
                "teacher": _names(period, "te", "teachers"),
                "status": status,
                "info": _clean(getattr(period, "info", None)),
                "subst_text": _clean(getattr(period, "substText", None)),
                "lesson_text": _clean(getattr(period, "lstext", None)),
            }
            entry["free"] = _is_free(entry, markers)
            periods.append(entry)
        periods.sort(key=lambda p: (p["date"], p["start"]))
        return periods

    def _gaps(self, periods: list[dict], cfg) -> list[dict[str, str]]:
        """Freistunden: Lücken zwischen den Stunden, bei denen man da sein muss.

        Die Schwelle trennt echte Freistunden von den Pausen — eine Lücke von
        20 Minuten zwischen zwei Stunden ist keine Freistunde.
        """
        minimum = cfg.get_int("UNTIS_GAP_MIN_MINUTES", DEFAULT_GAP_MINUTES)
        attended = [p for p in periods if _attended(p)]
        gaps = []
        for current, following in zip(attended, attended[1:]):
            if current["date"] != following["date"]:
                continue
            end = _to_minutes(current["end"])
            start = _to_minutes(following["start"])
            if start - end >= minimum:
                gaps.append(
                    {
                        "date": current["date"],
                        "from": current["end"],
                        "to": following["start"],
                        "minutes": start - end,
                    }
                )
        return gaps

    def _holiday_for(self, s, target_date: dt.date) -> dict[str, str] | None:
        try:
            for holiday in s.holidays():
                if holiday.start.date() <= target_date <= holiday.end.date():
                    return {
                        "name": holiday.name,
                        "start": holiday.start.date().isoformat(),
                        "end": holiday.end.date().isoformat(),
                    }
        except Exception:  # noqa: BLE001 - optionale Zusatzinfo
            return None
        return None

    def _exams(self, s, cfg, target_date: dt.date) -> list[dict[str, Any]]:
        lookahead = cfg.get_int("UNTIS_EXAM_LOOKAHEAD_DAYS", 14)
        if lookahead <= 0:
            return []
        try:
            exams = s.exams(start=target_date, end=target_date + dt.timedelta(days=lookahead))
        except Exception:  # noqa: BLE001 - viele Schulen geben Prüfungen nicht frei
            return []

        result = []
        for exam in exams:
            exam_date = exam.start.date()
            result.append(
                {
                    "date": exam_date.isoformat(),
                    "start": exam.start.strftime("%H:%M"),
                    "subject": _exam_subject(exam),
                    "name": _clean(exam._data.get("name")),
                    "days_until": (exam_date - target_date).days,
                }
            )
        result.sort(key=lambda e: e["date"])
        return result

    # -- Nicht dokumentierte REST-Endpunkte ------------------------------

    def _rest_get(self, s, cfg, path: str, params: dict) -> Any:
        """GET auf die WebUntis-REST-API mit der Session aus dem JSON-RPC-Login."""
        server = cfg.get("UNTIS_SERVER").replace("https://", "").replace("http://", "")
        school = cfg.get("UNTIS_SCHOOL")
        cookies = {
            "JSESSIONID": s.config["jsessionid"],
            # WebUntis erwartet den Schulnamen base64-kodiert mit führendem "_".
            "schoolname": '"_' + base64.b64encode(school.encode()).decode() + '"',
        }
        response = requests.get(
            f"https://{server}{path}",
            params=params,
            cookies=cookies,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=cfg.timeout,
        )
        response.raise_for_status()
        return response.json()

    def _homework(self, s, cfg, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        try:
            payload = self._rest_get(
                s,
                cfg,
                "/WebUntis/api/homeworks/lessons",
                {
                    "startDate": start.strftime("%Y%m%d"),
                    "endDate": (end + dt.timedelta(days=7)).strftime("%Y%m%d"),
                },
            )
        except Exception:  # noqa: BLE001 - Endpunkt existiert nicht überall
            return []

        records = (payload.get("data") or payload).get("homeworks") or []
        lessons = {l.get("id"): l for l in ((payload.get("data") or payload).get("lessons") or [])}

        result = []
        for item in records:
            if item.get("completed"):
                continue
            lesson = lessons.get(item.get("lessonId")) or {}
            result.append(
                {
                    "subject": lesson.get("subject") or "?",
                    "text": _clean(item.get("text")) or _clean(item.get("remark")) or "",
                    "due": _iso_from_untis(item.get("dueDate")),
                }
            )
        result.sort(key=lambda h: h["due"] or "9999")
        return result

    def _messages(self, s, cfg, target_date: dt.date) -> list[str]:
        for path, params, extract in (
            (
                "/WebUntis/api/public/news/newsWidgetData",
                {"date": target_date.strftime("%Y-%m-%d")},
                lambda p: [
                    _clean(m.get("text"))
                    for m in ((p.get("data") or {}).get("messagesOfDay") or [])
                ],
            ),
            (
                "/WebUntis/api/rest/view/v1/messages",
                {"date": target_date.strftime("%Y-%m-%d")},
                lambda p: [_clean(m.get("subject")) for m in (p.get("incomingMessages") or [])],
            ),
        ):
            try:
                payload = self._rest_get(s, cfg, path, params)
            except Exception:  # noqa: BLE001 - je nach Untis-Version nicht vorhanden
                continue
            messages = [m for m in extract(payload) if m]
            if messages:
                return messages
        return []

    # -- Darstellung -----------------------------------------------------

    def render(self, data: dict[str, Any], verbosity: str) -> str:
        blocks: list[str] = []
        periods = data.get("periods") or []
        multi_day = len({p["date"] for p in periods}) > 1

        if data.get("holiday"):
            blocks.append(f"Ferien/frei: {data['holiday']['name']}")

        if not periods and not data.get("holiday"):
            blocks.append("Kein Unterricht eingetragen.")

        if periods:
            blocks.append(self._render_periods(periods, verbosity, multi_day))

        gap_text = self._render_gaps(data, verbosity, multi_day)
        if gap_text:
            blocks.append(gap_text)

        exams = data.get("exams") or []
        if exams:
            blocks.append(self._render_exams(exams, verbosity))

        homework = data.get("homework") or []
        if homework:
            blocks.append(self._render_homework(homework, verbosity))

        messages = data.get("messages") or []
        if messages:
            if verbosity == "compact":
                blocks.append("Info: " + " | ".join(messages))
            else:
                blocks.append(
                    "**Nachrichten zum Tag**\n" + "\n".join(f"- {m}" for m in messages)
                )

        return "\n".join(block for block in blocks if block)

    def _render_periods(self, periods: list[dict], verbosity: str, multi_day: bool) -> str:
        if verbosity == "compact":
            groups: dict[str, list[str]] = {}
            for period in periods:
                label = period["subject"]
                if period["status"] == "cancelled":
                    label += " ENTF"
                elif period["free"]:
                    label += " FREI"
                elif period["status"] == "irregular":
                    label += f" VERTR{'/' + period['room'] if period['room'] else ''}"
                elif period["room"]:
                    label += f" {period['room']}"
                groups.setdefault(period["date"], []).append(f"{period['start']} {label}")
            if not multi_day:
                return "Std: " + " | ".join(next(iter(groups.values())))
            return "\n".join(
                f"{_short_date(date)}: " + " | ".join(entries)
                for date, entries in groups.items()
            )

        lines = []
        current_date = None
        for period in periods:
            if multi_day and period["date"] != current_date:
                current_date = period["date"]
                lines.append(f"**{_long_date(current_date)}**")
            marker = "FREI" if period["free"] else STATUS_LABELS.get(period["status"], "")
            parts = [f"{period['start']}–{period['end']}", period["subject"]]
            if period["room"]:
                parts.append(period["room"])
            if verbosity == "full" and period["teacher"]:
                parts.append(period["teacher"])
            line = " · ".join(parts)
            if marker:
                line += f" — **{marker}**"
            extra = period.get("subst_text") or period.get("info")
            if extra and verbosity == "full":
                line += f" ({extra})"
            elif extra and marker:
                line += f" ({extra})"
            lines.append(f"- {line}")
        return "\n".join(lines)

    def _render_gaps(self, data: dict, verbosity: str, multi_day: bool) -> str:
        lines = []
        periods = data.get("periods") or []

        # Nur für den eigentlichen Zieltag — im Wochenmodus wäre "erste Stunde
        # entfällt" über alle Tage hinweg sinnlos.
        today = data.get("date")
        todays = [p for p in periods if p["date"] == today]
        if todays and todays[0]["status"] == "cancelled":
            start = next((p["start"] for p in todays if p["status"] != "cancelled"), None)
            if start:
                lines.append(f"Erste Stunde entfällt — Schulbeginn erst {start}")

        for gap in data.get("gaps") or []:
            prefix = f"{_short_date(gap['date'])}: " if multi_day else ""
            lines.append(
                f"{prefix}Freistunde {gap['from']}–{gap['to']} ({gap['minutes']} min)"
            )

        if not lines:
            return ""
        if verbosity == "compact":
            return "; ".join(lines)
        return "\n".join(f"- {line}" for line in lines)

    def _render_exams(self, exams: list[dict], verbosity: str) -> str:
        if verbosity == "compact":
            return "Klausuren: " + " | ".join(
                f"{e['subject']} in {e['days_until']}T" for e in exams
            )
        lines = []
        for exam in exams:
            when = "heute" if exam["days_until"] == 0 else (
                "morgen" if exam["days_until"] == 1 else f"in {exam['days_until']} Tagen"
            )
            label = f"{exam['subject']} — {when} ({_short_date(exam['date'])}, {exam['start']})"
            if verbosity == "full" and exam.get("name"):
                label += f" · {exam['name']}"
            lines.append(f"- {label}")
        return "**Klausuren**\n" + "\n".join(lines)

    def _render_homework(self, homework: list[dict], verbosity: str) -> str:
        if verbosity == "compact":
            return "HA: " + " | ".join(
                f"{h['subject']}: {_truncate(h['text'], 40)}" for h in homework
            )
        lines = []
        for item in homework:
            due = f" (bis {_short_date(item['due'])})" if item.get("due") else ""
            lines.append(f"- {item['subject']}: {item['text']}{due}")
        return "**Hausaufgaben**\n" + "\n".join(lines)


# -- Hilfsfunktionen -----------------------------------------------------


def _is_free(entry: dict[str, Any], markers: tuple[str, ...]) -> bool:
    """Stunde, die im Plan steht, aber ohne Lehrkraft stattfindet.

    Untis lässt solche Stunden als `regular`/`irregular` stehen und vermerkt
    das nur im Freitext ("Vtr. ohne Lehrer"). Ausgefallene Stunden sind über
    den Status abgedeckt und zählen hier nicht mit, sonst würde die Ausgabe
    "FREI" statt "ENTFÄLLT" melden.
    """
    if entry["status"] == "cancelled":
        return False
    haystack = " ".join(
        entry.get(key) or "" for key in ("subst_text", "info", "lesson_text")
    ).lower()
    return any(marker in haystack for marker in markers)


def _attended(period: dict[str, Any]) -> bool:
    """Stunde, bei der man tatsächlich anwesend sein muss.

    Grundlage für Schulbeginn/-ende und für die Freistunden-Lücken: was
    entfällt oder ohne Lehrkraft ist, hält niemanden in der Schule.
    """
    return period["status"] != "cancelled" and not period.get("free")


def _names(period, raw_key: str, attr: str) -> str:
    """Namen aus einer Periode lesen — erst Rohdaten, dann Stammdaten.

    Die Rohdaten enthalten bei `my_timetable` meist schon `name`/`longname`
    und kosten keinen zusätzlichen Request.
    """
    raw = period._data.get(raw_key) or []
    names = [entry.get("name") or entry.get("longname") for entry in raw if isinstance(entry, dict)]
    names = [n for n in names if n]
    if names:
        return ", ".join(names)

    if attr in _DENIED_MASTERDATA:
        return ""

    try:
        objects = getattr(period, attr)
        values = [getattr(obj, "name", None) or getattr(obj, "long_name", None) for obj in objects]
        return ", ".join(v for v in values if v)
    except Exception:  # noqa: BLE001 - fehlende Rechte auf Stammdaten
        _DENIED_MASTERDATA.add(attr)
        return ""


def _exam_subject(exam) -> str:
    raw = exam._data.get("subject")
    if isinstance(raw, str) and raw:
        return raw
    try:
        return exam.subject.name
    except Exception:  # noqa: BLE001
        return str(raw or "?")


def _clean(value) -> str:
    return " ".join(str(value).split()) if value else ""


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _to_minutes(hhmm: str) -> int:
    hours, _, minutes = hhmm.partition(":")
    return int(hours) * 60 + int(minutes)


def _iso_from_untis(value) -> str | None:
    """WebUntis liefert Daten als int im Format YYYYMMDD."""
    if not value:
        return None
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _short_date(iso: str) -> str:
    try:
        date = dt.date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    from ..render import WEEKDAYS_DE

    return f"{WEEKDAYS_DE[date.weekday()]} {date.strftime('%d.%m.')}"


def _long_date(iso: str) -> str:
    try:
        date = dt.date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    from ..render import WEEKDAYS_DE_LONG

    return f"{WEEKDAYS_DE_LONG[date.weekday()]}, {date.strftime('%d.%m.')}"
