"""Tesla positions, read from a separate Tesla poller's SQLite database.

Family Locator never talks to Tesla itself: a poller (not part of this repo) owns the Tesla
Fleet API, its tokens and its sleep-safe schedule, and writes each car reading into a `reads`
table (ts, latitude, longitude, battery_level, battery_range, speed, shift_state, ...). This
source copies new rows that have a position into Family Locator as one ownerless "Car" item.

Off unless `tesla_db` is set in config.toml. The poller's DB is opened read-only.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from . import db
from .config import Config

log = logging.getLogger("famloc.tesla")
SCAN_SECONDS = 60


class TeslaSource:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = "off" if not cfg.tesla_db else "starting"  # off | ok | error
        self.state_detail = ""
        self.last_poll = 0.0
        self.counters = {"polls": 0, "poll_errors": 0, "new_points": 0}

    @property
    def dev_id(self) -> str:
        return "tesla:" + "".join(ch for ch in self.cfg.tesla_name.lower() if ch.isalnum())

    def status(self) -> dict:
        return {"state": self.state, "detail": self.state_detail, "last_poll": self.last_poll,
                "db": self.cfg.tesla_db, **self.counters}

    def run_forever(self, stop: threading.Event) -> None:
        if not self.cfg.tesla_db:
            return
        while not stop.is_set():
            self.poll_once()
            stop.wait(SCAN_SECONDS)

    def poll_once(self) -> int:
        self.counters["polls"] += 1
        self.last_poll = time.time()
        path = Path(self.cfg.tesla_db).expanduser()
        since = float(db.get_setting("tesla_last_ts", 0) or 0)
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    "SELECT ts, latitude, longitude, battery_level, battery_range, speed, shift_state, "
                    "charging_state FROM reads WHERE ts > ? AND latitude IS NOT NULL ORDER BY ts",
                    (since,)).fetchall()
            finally:
                con.close()
        except sqlite3.Error as e:
            self.counters["poll_errors"] += 1
            self.state, self.state_detail = "error", f"{type(e).__name__}: {e}"
            return 0
        new = 0
        if rows:
            last = rows[-1]
            batt = last["battery_level"] / 100 if last["battery_level"] is not None else None
            charging = last["charging_state"] in ("Charging", "Starting")
            db.upsert_device(self.dev_id, "tesla", self.cfg.tesla_name, owner=None, model="Tesla",
                             device_class="Car", battery=batt,
                             battery_status="Charging" if charging else "NotCharging")
            for r in rows:
                meta = {k: r[k] for k in ("speed", "shift_state", "battery_range") if r[k] is not None}
                if db.add_point(self.dev_id, float(r["ts"]), r["latitude"], r["longitude"], acc=10.0,
                                battery=r["battery_level"] / 100 if r["battery_level"] is not None else None,
                                kind="tesla", meta=meta or None):
                    new += 1
            db.set_setting("tesla_last_ts", float(last["ts"]))
        self.counters["new_points"] += new
        self.state, self.state_detail = "ok", f"{new} new position(s)" if new else "no new positions"
        return new
