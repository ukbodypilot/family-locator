"""SQLite storage. One connection per thread; WAL so the web UI never blocks the pollers."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from .config import DATA_DIR

DB_PATH = DATA_DIR / "famloc.db"
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id          TEXT PRIMARY KEY,          -- '<source>:<stable id>'
    source      TEXT NOT NULL,             -- icloud | airtag | photos
    name        TEXT NOT NULL,
    owner       TEXT,
    model       TEXT,
    device_class TEXT,
    color       TEXT,
    hidden      INTEGER NOT NULL DEFAULT 0,
    first_seen  REAL,
    last_seen   REAL,                      -- ts of newest stored fix
    last_poll   REAL,                      -- last time a poll saw this device at all
    battery     REAL,                      -- 0..1, latest
    battery_status TEXT,
    meta        TEXT                       -- json
);
CREATE TABLE IF NOT EXISTS points (
    id        INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id),
    ts        REAL NOT NULL,               -- unix seconds UTC, time of the FIX
    lat       REAL NOT NULL,
    lon       REAL NOT NULL,
    acc       REAL,                        -- horizontal accuracy, metres
    alt       REAL,
    battery   REAL,
    kind      TEXT,                        -- GPS / Wifi / Cell / airtag / photo ...
    meta      TEXT,
    UNIQUE(device_id, ts)
);
CREATE INDEX IF NOT EXISTS points_dev_ts ON points(device_id, ts);
CREATE INDEX IF NOT EXISTS points_ts ON points(ts);
CREATE TABLE IF NOT EXISTS geofences (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    lat       REAL NOT NULL,
    lon       REAL NOT NULL,
    radius    REAL NOT NULL,
    color     TEXT,
    on_enter  INTEGER NOT NULL DEFAULT 1,
    on_exit   INTEGER NOT NULL DEFAULT 1,
    devices   TEXT                         -- json list of device ids; null = all
);
CREATE TABLE IF NOT EXISTS fence_state (
    geofence_id INTEGER NOT NULL,
    device_id   TEXT NOT NULL,
    inside      INTEGER NOT NULL,
    since       REAL,
    PRIMARY KEY (geofence_id, device_id)
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,               -- enter / exit / battery / stale / auth / error / info
    device_id TEXT,
    geofence_id INTEGER,
    message   TEXT NOT NULL,
    emailed   INTEGER NOT NULL DEFAULT 0,
    email_error TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS web_sessions (
    token   TEXT PRIMARY KEY,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS locate_events (
    id      INTEGER PRIMARY KEY,
    ts      REAL NOT NULL,
    event   TEXT NOT NULL,     -- request / reply / answer / fix / unanswered / backoff / pause / resume / blocked / follow_start / follow_stop
    person  TEXT,
    data    TEXT               -- json
);
CREATE INDEX IF NOT EXISTS locate_events_ts ON locate_events(ts);
CREATE TABLE IF NOT EXISTS geocode_cache (
    q       TEXT PRIMARY KEY,
    ts      REAL NOT NULL,
    result  TEXT NOT NULL
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=FULL")  # every fix on disk before commit returns
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init() -> None:
    conn().executescript(SCHEMA)
    cols = {r["name"] for r in q("PRAGMA table_info(geofences)")}
    if "people" not in cols:  # v0.3: alert rules can target people (device owners)
        x("ALTER TABLE geofences ADD COLUMN people TEXT")
    try:
        DB_PATH.chmod(0o600)
    except OSError:
        pass


def q(sql: str, args: Iterable[Any] = ()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, tuple(args)).fetchall()]


def q1(sql: str, args: Iterable[Any] = ()) -> dict | None:
    r = conn().execute(sql, tuple(args)).fetchone()
    return dict(r) if r else None


def x(sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
    return conn().execute(sql, tuple(args))


def get_setting(key: str, default: Any = None) -> Any:
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    if r is None:
        return default
    return json.loads(r["value"])


def set_setting(key: str, value: Any) -> None:
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(value)))


PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6",
           "#bfef45", "#469990", "#9a6324", "#800000", "#808000", "#000075", "#fabed4"]


def upsert_device(dev_id: str, source: str, name: str, *, owner: str | None = None,
                  model: str | None = None, device_class: str | None = None,
                  battery: float | None = None, battery_status: str | None = None,
                  meta: dict | None = None) -> None:
    now = time.time()
    existing = q1("SELECT id FROM devices WHERE id=?", (dev_id,))
    if existing is None:
        n = q1("SELECT COUNT(*) AS n FROM devices")["n"]
        x("INSERT INTO devices(id,source,name,owner,model,device_class,color,first_seen,last_poll,"
          "battery,battery_status,meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
          (dev_id, source, name, owner, model, device_class, PALETTE[n % len(PALETTE)], now, now,
           battery, battery_status, json.dumps(meta or {})))
    else:
        x("UPDATE devices SET name=?, owner=COALESCE(?,owner), model=COALESCE(?,model), "
          "device_class=COALESCE(?,device_class), last_poll=?, battery=COALESCE(?,battery), "
          "battery_status=COALESCE(?,battery_status), meta=COALESCE(?,meta) WHERE id=?",
          (name, owner, model, device_class, now, battery, battery_status,
           json.dumps(meta) if meta is not None else None, dev_id))


def add_point(dev_id: str, ts: float, lat: float, lon: float, *, acc: float | None = None,
              alt: float | None = None, battery: float | None = None, kind: str | None = None,
              meta: dict | None = None) -> bool:
    """Insert a fix. Returns True if it was new (not a duplicate of (device, ts))."""
    cur = x("INSERT OR IGNORE INTO points(device_id,ts,lat,lon,acc,alt,battery,kind,meta) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (dev_id, ts, lat, lon, acc, alt, battery, kind, json.dumps(meta) if meta else None))
    if cur.rowcount:
        x("UPDATE devices SET last_seen=MAX(COALESCE(last_seen,0),?) WHERE id=?", (ts, dev_id))
        return True
    return False


def add_event(kind: str, message: str, *, device_id: str | None = None,
              geofence_id: int | None = None, ts: float | None = None) -> int:
    cur = x("INSERT INTO events(ts,kind,device_id,geofence_id,message) VALUES(?,?,?,?,?)",
            (ts or time.time(), kind, device_id, geofence_id, message))
    return cur.lastrowid


BACKUP_DIR = DATA_DIR / "backups"


def backup(keep: int = 14) -> str:
    """Consistent online copy (sqlite backup API) to data/backups/famloc-YYYY-MM-DD.db; prune old."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.chmod(0o700)
    dst = BACKUP_DIR / f"famloc-{time.strftime('%Y-%m-%d')}.db"
    tmp = dst.with_suffix(".tmp")
    out = sqlite3.connect(tmp)
    try:
        conn().backup(out)
    finally:
        out.close()
    tmp.chmod(0o600)
    tmp.replace(dst)
    for old in sorted(BACKUP_DIR.glob("famloc-*.db"))[:-keep]:
        old.unlink()
    set_setting("last_backup", {"ts": time.time(), "path": str(dst), "bytes": dst.stat().st_size})
    return str(dst)


def person_display(owner: str | None) -> str | None:
    """Display name for a device owner (Apple's family names can be abbreviated, e.g. "J D")."""
    if not owner:
        return owner
    return (get_setting("person_names", {}) or {}).get(owner) or owner


def log_locate_event(event: str, person: str | None, ts: float | None = None, **data) -> None:
    """Durable record of the locate governor's decisions and Apple's replies, for tuning from data.
    (The system journal on this box is in RAM.) Kept 30 days; see tools/locate_report.py."""
    try:
        x("INSERT INTO locate_events(ts,event,person,data) VALUES(?,?,?,?)",
          (ts or time.time(), event, person, json.dumps(data, default=str) if data else None))
    except sqlite3.Error:
        pass
