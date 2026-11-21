# Family Locator

Self-hosted location history, geofence alerts and a map UI for every device in an iCloud Family
Sharing group, plus AirTags and photo-GPS backfill.

- **iCloud Find My** (via [pyicloud](https://github.com/timlaing/pyicloud)): your devices and your
  family's (`with_family`). Polls Apple's cached positions every `poll_seconds`, and asks for a
  fresh fix every `locate_minutes`.
- **AirTags** (via [FindMy.py](https://github.com/malmeloo/FindMy.py)): needs key files decrypted
  on a Mac. Returns several days of history per poll.
- **Photo history**: GPS from iCloud Photos metadata (no image download) or from a local folder's EXIF.
- **Polling + alert settings editable in the UI** (Settings → Polling & alerts); per-device "⟳ locate now".
- **History is durable**: SQLite on disk, `synchronous=FULL`, daily online backup to `data/backups/` (14 kept).
- **Alerts by email**: arrive/leave a place (with hysteresis), low battery, phone/iPad/Watch stale, iCloud
  re-login needed.
- **Map UI**: 10 base maps (OSM, Esri streets/grey/dark/satellite/hybrid/topo, OpenTopoMap, CyclOSM,
  Humanitarian); overlays for tracks, accuracy, dots, heatmap, stays, places and photos; search for
  people, places, addresses and `lat, lon`; a timeline with presets, custom range, a per-device
  activity histogram, scrubbing, playback at 1 min/s to 1 week/s, and trail length; right-click
  "what's here / create place"; Insights (km, trips, stays, top places, distance per day).

## Credentials

- The **Apple ID password is never written to disk.** You type it in the Settings tab. Only
  pyicloud's session/trust cookies persist, in `data/icloud_session/` (0700). When Apple
  expires them you get an email and log in again.
- FindMy.py's saved account state has its password field blanked before it is saved.
- The web UI has its own password, set on first visit (PBKDF2, login throttling, `[WebAccess]` log lines).
- Search and "what's here?" go to OpenStreetMap Nominatim. Only what you type or click is sent;
  device positions are never sent automatically.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.toml data/config.toml   # fill in email settings
.venv/bin/python -m famloc.main           # http://<host>:8790
```

On .140 it runs as a user service (linger is on):

```bash
ln -sf ~/Downloads/family-locator/deploy/family-locator.service ~/.config/systemd/user/
systemctl --user enable --now family-locator
journalctl --user-unit family-locator -f
```

## AirTag keys

On a Mac signed in to the same Apple ID: `pip install findmy && python -m findmy decrypt --out-dir keys`.
Copy the JSON files into `data/airtags/`. Recent macOS versions make the Find My cache harder to
decrypt, so expect some fiddling.

## Layout

| path | what |
|---|---|
| `famloc/icloud_source.py` | pyicloud login/2FA state machine + poller |
| `famloc/airtag_source.py` | FindMy.py login + AirTag history poller (single worker thread) |
| `famloc/photos_import.py` | iCloud Photos / folder EXIF backfill |
| `famloc/alerts.py` | geofence, battery, stale checks; SMTP |
| `famloc/analysis.py` | stays / trips / per-day distance |
| `famloc/web.py` | FastAPI API + auth; `/metrics` for Prometheus |
| `famloc/static/` | Leaflet UI (vendored, no CDN) |
| `data/` | gitignored: SQLite DB, config, sessions, AirTag keys |

Tests: `FAMLOC_DATA=$(mktemp -d) .venv/bin/python tests/test_core.py`.
Demo data for UI work: `FAMLOC_DATA=~/famloc-scratch/data .venv/bin/python tools/seed_demo.py`.

Tell your family members their locations are being logged.
