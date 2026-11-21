# How it works

A single Python process (FastAPI + uvicorn) runs a web server, two poller threads and a
housekeeping thread, all sharing one SQLite database. The browser UI is vanilla JavaScript and
Leaflet, with no build step.

```
             ┌────────────── famloc.main ───────────────┐
 iCloud ◀────┤ icloud thread   (pyicloud, Find My)      │
 Find My net◀┤ airtag thread   (FindMy.py, one worker)  │──▶ SQLite data/famloc.db
             │ housekeeping    (stale check, backup,    │       ▲
 Gmail ◀─────┤                  daily summary)          │       │
             │ uvicorn: famloc.web (API + static UI)    │───────┘
             └──────────────────────────────────────────┘
                     ▲  :8790 direct, or /family/ via the radio gateway
                  browser (famloc/static: index.html, app.js, app.css)
```

## Modules

| File | Role |
|---|---|
| `famloc/main.py` | Starts everything; housekeeping loop: stale-device check, daily backup, daily summary. |
| `famloc/config.py` | `Config` defaults, `config.toml` loading, the UI-editable settings and their limits. |
| `famloc/db.py` | Schema and migrations, one connection per thread (WAL, `synchronous=FULL`), helpers, backup. |
| `famloc/icloud_source.py` | pyicloud sign-in and 2FA, session resume, the 15-second tick, single-device locates, saving fixes. |
| `famloc/tracking.py` | Per-person locate scheduling, movement and rest detection, follow mode. Pure logic, unit-tested. |
| `famloc/airtag_source.py` | FindMy.py sign-in and AirTag history fetch, on one worker thread (its sync wrapper owns an asyncio loop). |
| `famloc/photos_import.py` | Background imports from iCloud Photos metadata and from folder EXIF. |
| `famloc/alerts.py` | Place, battery and stale checks; quiet hours; per-person dedupe; SMTP; links; the daily summary. |
| `famloc/analysis.py` | Stays, trips, per-day distance and top places, computed on request. |
| `famloc/geocode.py` | Nominatim search and reverse lookup, cached 30 days, rate-limited to 1 per second. |
| `famloc/web.py` | API routes, page-password auth, login throttling, method override, `/metrics`. |
| `famloc/static/` | The UI. Leaflet, markercluster, leaflet-heat and fonts are vendored in `vendor/`. |

## Data model

| Table | Holds |
|---|---|
| `devices` | One row per device: `id` (`icloud:<discovery id>`, `airtag:<id>`, `photos:icloud`), source, name, owner (Apple's family member name), model, class, hidden, latest battery, `last_seen`. |
| `points` | Every fix: device, `ts` (fix time, Unix seconds), lat, lon, accuracy, altitude, battery, kind, meta. Unique on (device, ts), so re-reading the same fix is a no-op. |
| `geofences` | Places: centre, radius, colour, arrive/leave switches, `people` and `devices` it's limited to. |
| `fence_state` | Inside or outside, per place and device, and since when. |
| `events` | Everything on the Alerts tab, with whether it was emailed and any email error. |
| `settings` | Key/value JSON: UI password hash, Apple ID, setting overrides, person colours and names, alert flags, last backup. |
| `web_sessions`, `geocode_cache` | Page sessions; cached geocoder answers. |

A **person** is a device `owner`. People aren't stored separately; colours and display names are
kept in `settings` under the owner's Apple name.

## Polling

The iCloud thread ticks every 15 seconds:

1. **Fresh locations.** `famloc/tracking.py` decides which people are due: their own interval, or 2
   minutes while followed. For each one, the thread sends Find My's single-device locate
   (`selectedDevice`) for their main device, and returns straight away.
2. **Reading.** It refreshes Find My, including family members' devices, every `poll_seconds`,
   or every 10 seconds for 50 seconds after a fresh-location request, to collect the answer.
3. **Each device:** it upserts the device row and stores the fix if its timestamp is new, then runs
   the place checks. Battery is checked on every read.
4. **New fixes from a person's phone, Watch or iPad:** these feed the movement check, which starts,
   extends or ends following (see [Tracking and following](tracking.md)).
5. **Session failure:** a failed session moves the state to `need_login` and sends one email.

**Locate now** on a device blocks for up to 36 seconds and re-reads every 6 seconds, so the UI can
report the result. **Locate everyone now** sends one all-devices locate.

## How the UI places people

All of this is client-side in `app.js`:

- **Position:** the freshest fix among the person's carried devices (iPhone, Watch, iPad) at the
  cursor time. An iPhone wins ties within 15 minutes.
- **Main device:** for tracks, lanes and Insights, the carried device with the most fixes in the
  range.
- **Lane colour:** each fix gets *inside place X*; or *moving* (more than 250 m from a neighbouring
  fix within 45 minutes); or *elsewhere*. Runs stop at gaps longer than 3 hours.
- **Merged markers:** people merge when they're at the same place, or their markers would overlap
  on screen.

## HTTP API

Every route except `/`, `/static/*`, `/metrics` and the `auth` routes needs the session cookie.
Bodies are JSON. Behind the gateway, PUT, PATCH and DELETE arrive as POST with
`X-HTTP-Method-Override`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/auth/state` | Is a password set; are you signed in; version. |
| POST | `/api/auth/setup` | Set the first password `{password}`. |
| POST | `/api/auth/login`, `/api/auth/logout` | Sign in or out. |
| POST | `/api/auth/change` | `{old, new}`. |
| GET | `/api/devices` | All devices with their latest fix and fix count. |
| PATCH | `/api/devices/{id}` | `{name?, hidden?, color?}`. |
| GET | `/api/people` | `[{name, display, color, tracking}]`; tracking includes the effective interval, following state and next fresh location. |
| PATCH | `/api/people/{name}` | `{display?, color?, locate_minutes?, follow?}`; `null` resets to the default. |
| POST | `/api/people/{name}/follow` | `{minutes}`: follow now; 0 stops. |
| GET | `/api/points?start&end[&devices][&max_acc]` | Fixes in a range, grouped by device: `[ts, lat, lon, acc, battery, kind, meta]`. |
| GET | `/api/range` | First and last fix time, and the count. |
| GET | `/api/analysis?device&start&end` | km, stays, trips, per-day km, top places. |
| GET, POST | `/api/geofences` | List (with who's inside) or create a place. |
| PUT, DELETE | `/api/geofences/{id}` | Update or delete a place. |
| GET | `/api/events?limit&kind` | Recent activity. |
| GET, PUT | `/api/config` | UI-editable settings. |
| GET | `/api/status` | Source states, counters, email state, links, backup, storage. |
| POST | `/api/icloud/login`, `/2fa`, `/sms`, `/logout` | iCloud sign-in flow. |
| POST | `/api/icloud/poll?locate=true` | Locate everyone now. |
| POST | `/api/icloud/locate/{device id}` | Locate one device; returns `{updated, fix_ts, waited_s}`. |
| POST | `/api/airtag/login`, `/2fa`, `/logout`, `/poll` | AirTag sign-in and fetch. |
| POST | `/api/photos/icloud`, `/api/photos/folder` | Start a photo import. |
| DELETE | `/api/photos/{icloud\|folder}` | Remove an imported photo source. |
| GET | `/api/digest`; POST `/api/digest/send` | Preview or send the daily summary. |
| POST | `/api/email/test` | Send a test email. |
| GET | `/api/geocode?q[&lat&lon]`, `/api/reverse?lat&lon` | Address search and "What's here?". |
| GET | `/metrics` | Prometheus text: source state, polls, errors, new fixes, emails, point count. |

## Tests

`tests/test_core.py` covers place enter/exit with the margin, ignoring stale and inaccurate fixes,
one email per person, people filters, quiet hours, battery alert re-arming, stays and trips,
duplicate fixes, display names, the daily summary, the tracker (intervals, staggering, movement versus GPS wobble, follow and rest, the safety cap, manual follow), and a simulated drive through the real poller against a fake Find My.

```bash
FAMLOC_DATA=$(mktemp -d) .venv/bin/python tests/test_core.py
```
