# hermes-daily

Ein Aufruf, ein Tagescheckup. Sammelt Stundenplan, Termine, Aufgaben, Abfahrten,
Wetter und Nachrichten ein und gibt sie als Markdown oder JSON aus — gedacht als
einzelnes Tool, das dein KI-Agent morgens ausführt, statt zehn Einzelabfragen zu machen.

```
$ ./bin/hermes-daily
08.09. Di
Std: 07:55 ER G1 A120 | 09:10 M L1 A223 | 10:35 M L1 A223 | 11:50 IF L1 Comp1
Freistunde 10:15–10:35 (20 min); Freistunde 12:55–13:15 (20 min)
HA: D G1: Interpretation Hölderlin: Mitte des Leb…
Todo: !Mathe-AB abgeben | Referat Bio vorbereiten
Abfahrten: 16:12 S3→Backnang | 16:20 S2→Schorndorf | 16:25 S1→Kirchheim(Teck)
Wetter Grevenbroich: 19→25°C, 73%R, leichter Nieselregen, Regen ab 07:00
Pack: Regenjacke (73% Regen ab 07:00)
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Dann die `.env` ausfüllen. Drei Helfer nehmen dir das Nachschlagen ab:

```bash
./bin/hermes-daily --geocode "Stuttgart"              # WEATHER_LAT / WEATHER_LON
./bin/hermes-daily --untis-discover "Schulname"       # UNTIS_SERVER / UNTIS_SCHOOL
./bin/hermes-daily --transit-search "Haltestelle"     # TRANSIT_STOP_ID
```

Und `--check` zeigt jederzeit, was noch fehlt:

```bash
$ ./bin/hermes-daily --check
Module:
  [x] untis     bereit
  [!] calendar  weder CALENDAR_ICS_URLS noch CALDAV_URL gesetzt
  [ ] news      abgeschaltet (ENABLE_NEWS=false)
```

> **Achtung bei der `.env`:** Kommentare gehören in eine eigene Zeile.
> Bei einem leeren Wert würde `KEY=   # Kommentar` den Kommentar als Wert setzen —
> `python-dotenv` entfernt Inline-Kommentare nur, wenn ein Wert davorsteht.

## Aufruf durch den Agenten

Der Agent führt genau einen Befehl aus und muss nichts über venvs wissen:

```
/PATH/bin/hermes-daily
```

Standardmäßig kommt die `compact`-Fassung — dicht gepackt, keine Leerzeilen,
leere Abschnitte fallen weg. Für die tägliche Zusammenfassung reicht das und
kostet rund ein Drittel der Tokens der ausführlichen Fassung.

## Module

| Modul | Quelle | Braucht |
|---|---|---|
| `weather` | Open-Meteo (Forecast + Air-Quality) | Koordinaten, kein API-Key |
| `untis` | WebUntis JSON-RPC + REST | Zugangsdaten deiner Schule |
| `calendar` | ICS-URLs und/oder CalDAV | Feed-URL bzw. CalDAV-Zugang |
| `news` | beliebige RSS-/Atom-Feeds | Feed-URLs |
| `tasks` | lokale Markdown-Checkliste | Pfad zur Datei |
| `transit` | dbf.finalrewind.org oder transport.rest | Stationsname bzw. Stop-ID |
| `advice` | leitet sich aus Wetter + Stundenplan ab | — |

Jedes Modul wird über `ENABLE_<NAME>` in der `.env` an- und abgeschaltet.

**Untis** liefert Stundenplan mit Vertretungen und Entfall, Freistunden,
Klausuren-Countdown, Hausaufgaben, Tagesnachrichten und Ferien.

**Aufgaben** liest `- [ ] Aufgabe` aus einer Markdown-Datei; ein optionales
`@2026-09-10` markiert die Fälligkeit, Überfälliges wird hervorgehoben.

**Advice** ist reine Heuristik ohne LLM-Aufruf: Regenjacke bei hoher
Regenwahrscheinlichkeit, Zwiebellook bei großer Tagesspanne, Sportsachen wenn
Sport im Plan steht (erweiterbar über `ADVICE_SUBJECT_HINTS`).

## Ausgabe

```bash
./bin/hermes-daily                    # compact (Default)
./bin/hermes-daily --verbosity normal # Markdown mit Überschriften
./bin/hermes-daily --full             # zusätzlich Lehrer, Teaser, Beschreibungen
./bin/hermes-daily --format json      # immer vollständig, unabhängig von der Verbosity
```

Die Verbosity greift ausschließlich im Markdown-Renderer — das Maschinenformat
bleibt bewusst verlustfrei. `HERMES_MAX_CHARS` kappt die Ausgabe zusätzlich hart.

Weitere Schalter:

```bash
./bin/hermes-daily --date 2026-09-09     # anderer Zieltag
./bin/hermes-daily --week                # Wochenübersicht bis Freitag
./bin/hermes-daily --only weather,untis  # nur diese Module (hebt .env-Flags auf)
./bin/hermes-daily --skip news           # dieses Modul auslassen
```

## Robustheit

Ein Morgen-Tool darf nicht komplett scheitern, weil ein RSS-Feed hustet:

- Jedes Modul läuft in einem eigenen Thread mit eigenem Timeout (`HERMES_TIMEOUT`).
- Fällt eines aus, erscheint dort eine gekürzte Fehlerzeile — alle anderen
  Abschnitte werden trotzdem gerendert.
- Der Exit-Code ist `0`, solange gerendert werden konnte; `1` nur bei
  Konfigurationsfehlern. Der Agent bekommt also immer eine verwertbare Ausgabe.
- Innerhalb des Kalender-Moduls gilt das auch pro Quelle: ein kaputter CalDAV-Server
  verhindert die ICS-Termine nicht.

## Verlauf und Änderungen

Bei `ENABLE_HISTORY=true` landet jeder Lauf unter `history/YYYY-MM-DD.json`
(gitignored, enthält persönliche Daten). Läuft das Tool erneut für denselben Tag,
wird gegen den letzten Snapshot verglichen:

```
Änderungen: NEU: 09:10 GE G1 (FORUM) — Vertretung; RAUM: 10:35 M L1 A223 → A118
```

Ein fehlgeschlagener Untis-Abruf erzeugt bewusst **keinen** Diff — sonst würde
der komplette Stundenplan als „entfernt“ gemeldet.

## Bekannte Einschränkungen

- **Hausaufgaben und Tagesnachrichten** laufen über REST-Endpunkte, die Untis nicht
  offiziell dokumentiert und die es nicht auf jedem Server gibt. Antwortet einer
  bei dir dauerhaft leer, schalte ihn über `UNTIS_FETCH_HOMEWORK` bzw.
  `UNTIS_FETCH_MESSAGES` ab — er kostet sonst nur Wartezeit.
- **Stammdaten** (Lehrernamen) erfordern Rechte, die Schüler-Accounts meist nicht
  haben. Namen aus dem Stundenplan werden verwendet, Fehlendes bleibt leer.
- **`TRANSIT_PROVIDER=hafas_rest`** ist mit der DB-Instanz derzeit nicht nutzbar:
  `v6.db.transport.rest` antwortet mit 503, seit das HAFAS-Backend abgeschaltet
  wurde. `v6.bvg` und `v6.vbb` laufen. Deshalb ist `dbf` der Default — der deckt
  bundesweit DB-Stationen ab (S-Bahn, RE, IC), aber keine reinen Stadtbusse/Trams.
- **CalDAV** ist gegen einen Fehlerfall getestet, aber nicht gegen einen echten
  Server — der ICS-Pfad dagegen inklusive Serien- und Ganztagesterminen.
