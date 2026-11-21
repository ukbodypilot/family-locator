"""Alerting: geofence enter/exit, low battery, stale devices, login problems. Email via SMTP."""

from __future__ import annotations

import json
import logging
import math
import smtplib
import socket
import threading
import time
from email.mime.text import MIMEText

from . import db
from .config import Config

log = logging.getLogger("famloc.alerts")
_email_lock = threading.Lock()

STALE_CLASSES = ("iPhone", "iPad", "Watch")
COUNTERS = {"emails_sent": 0, "emails_failed": 0, "events": 0}


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def lan_url(cfg: Config) -> str:
    if cfg.public_url:
        return cfg.public_url.rstrip("/")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.2.1", 80))
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        ip = "localhost"
    return f"http://{ip}:{cfg.port}"


class Alerter:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    # ---------- email ----------
    def email_configured(self) -> bool:
        c = self.cfg
        return bool(c.email_enabled and c.email_address and c.email_app_password and c.email_recipient)

    def send_email(self, subject: str, body: str) -> str | None:
        """Returns None on success, else the error text."""
        if not self.email_configured():
            return "email not configured"
        c = self.cfg
        msg = MIMEText(body + f"\n\n-- family-locator  {lan_url(c)}\n", "plain", "utf-8")
        msg["From"] = c.email_address
        msg["To"] = c.email_recipient
        msg["Subject"] = f"[Family Locator] {subject}"
        try:
            with _email_lock, smtplib.SMTP_SSL(c.smtp_host, c.smtp_port, timeout=20) as s:
                s.login(c.email_address, c.email_app_password)
                s.sendmail(c.email_address, [r.strip() for r in c.email_recipient.split(",")], msg.as_string())
            COUNTERS["emails_sent"] += 1
            return None
        except Exception as e:  # noqa: BLE001 - report every failure, never crash a poller
            COUNTERS["emails_failed"] += 1
            log.warning("email failed: %s", e)
            return str(e)

    def notify(self, kind: str, subject: str, message: str, *, device_id=None, geofence_id=None,
               ts=None, email=True) -> None:
        eid = db.add_event(kind, message, device_id=device_id, geofence_id=geofence_id, ts=ts)
        COUNTERS["events"] += 1
        log.info("event %s: %s", kind, message)
        if email and kind != "auth" and self.in_quiet_hours():
            db.x("UPDATE events SET email_error='quiet hours' WHERE id=?", (eid,))
            return
        if email and self.email_configured():
            # Send off-thread: SMTP must never stall a poller.
            def _send():
                err = self.send_email(subject, message)
                db.x("UPDATE events SET emailed=?, email_error=? WHERE id=?", (0 if err else 1, err, eid))
            threading.Thread(target=_send, daemon=True, name="email").start()

    def in_quiet_hours(self, now: float | None = None) -> bool:
        a, b = self.cfg.quiet_from_hour, self.cfg.quiet_to_hour
        if a < 0 or b < 0 or a == b:
            return False
        h = time.localtime(now or time.time()).tm_hour
        return a <= h < b if a < b else (h >= a or h < b)

    def _person_already_alerted(self, owner: str | None, geofence_id: int, kind: str, ts: float) -> bool:
        """Phone and watch cross a fence together; alert once per person."""
        if not owner:
            return False
        win = self.cfg.person_dedupe_minutes * 60
        r = db.q1("SELECT e.id FROM events e JOIN devices d ON d.id=e.device_id WHERE e.geofence_id=? "
                  "AND e.kind=? AND d.owner=? AND ABS(e.ts-?)<=?", (geofence_id, kind, owner, ts, win))
        return r is not None

    # ---------- per-fix checks ----------
    def check_fix(self, dev: dict, ts: float, lat: float, lon: float, acc: float | None) -> None:
        """Run geofence logic for a NEW fix. Only fixes newer than the fence state count."""
        if acc is not None and acc > self.cfg.max_alert_accuracy_m:
            return
        acc = acc or 0.0
        for g in db.q("SELECT * FROM geofences"):
            people = json.loads(g["people"]) if g.get("people") else []
            devs = json.loads(g["devices"]) if g["devices"] else []
            if (people or devs) and dev.get("owner") not in people and dev["id"] not in devs:
                continue
            d = haversine_m(lat, lon, g["lat"], g["lon"])
            st = db.q1("SELECT * FROM fence_state WHERE geofence_id=? AND device_id=?", (g["id"], dev["id"]))
            if st and st["since"] and ts <= st["since"]:
                continue  # older fix (e.g. AirTag history backfill) - don't rewrite the present
            # inside: confidently within radius; outside: confidently beyond radius+margin
            inside_now = d <= g["radius"]
            outside_now = d - min(acc, g["radius"]) > g["radius"] + self.cfg.geofence_exit_margin_m
            if st is None:
                if inside_now or outside_now:  # first observation sets state silently
                    db.x("INSERT INTO fence_state VALUES(?,?,?,?)", (g["id"], dev["id"], int(inside_now), ts))
                continue
            who = db.person_display(dev["owner"]) if dev.get("owner") else dev["name"]
            via = f" ({dev['name']})" if dev.get("owner") else ""
            when = time.strftime("%H:%M %a %d %b", time.localtime(ts))
            maps = f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=16/{lat:.5f}/{lon:.5f}"
            if inside_now and not st["inside"]:
                db.x("UPDATE fence_state SET inside=1, since=? WHERE geofence_id=? AND device_id=?",
                     (ts, g["id"], dev["id"]))
                if g["on_enter"]:
                    dup = self._person_already_alerted(dev.get("owner"), g["id"], "enter", ts)
                    self.notify("enter", f"{who} arrived at {g['name']}",
                                f"{who} arrived at {g['name']} at {when}{via}.\n{maps}",
                                device_id=dev["id"], geofence_id=g["id"], ts=ts, email=not dup)
            elif outside_now and st["inside"]:
                db.x("UPDATE fence_state SET inside=0, since=? WHERE geofence_id=? AND device_id=?",
                     (ts, g["id"], dev["id"]))
                if g["on_exit"]:
                    dup = self._person_already_alerted(dev.get("owner"), g["id"], "exit", ts)
                    self.notify("exit", f"{who} left {g['name']}",
                                f"{who} left {g['name']} at {when}{via} (now {d:.0f} m away).\n{maps}",
                                device_id=dev["id"], geofence_id=g["id"], ts=ts, email=not dup)

    def check_battery(self, dev: dict, battery: float | None) -> None:
        if battery is None or battery <= 0 or self.cfg.low_battery_pct <= 0:
            return
        pct = battery * 100
        key = f"lowbatt:{dev['id']}"
        flagged = db.get_setting(key, False)
        if pct <= self.cfg.low_battery_pct and not flagged:
            db.set_setting(key, True)
            who = dev["name"] + (f" ({db.person_display(dev['owner'])})" if dev.get("owner") else "")
            self.notify("battery", f"{who} battery {pct:.0f}%", f"{who} battery is at {pct:.0f}%.",
                        device_id=dev["id"])
        elif pct > self.cfg.low_battery_pct + 10 and flagged:
            db.set_setting(key, False)

    def check_stale(self) -> None:
        if self.cfg.stale_hours <= 0:
            return
        now = time.time()
        for dev in db.q("SELECT * FROM devices WHERE source IN ('icloud','airtag') AND hidden=0"):
            if not dev["last_seen"]:
                continue
            # AirPods / Macs routinely go quiet for days; only carried devices are worth an alert.
            if dev["source"] == "icloud" and not any(k in (dev["device_class"] or "") for k in STALE_CLASSES):
                continue
            key = f"stale:{dev['id']}"
            flagged = db.get_setting(key, False)
            age_h = (now - dev["last_seen"]) / 3600
            if age_h >= self.cfg.stale_hours and not flagged:
                db.set_setting(key, True)
                who = dev["name"] + (f" ({db.person_display(dev['owner'])})" if dev.get("owner") else "")
                self.notify("stale", f"{who} has not reported for {age_h:.0f} h",
                            f"{who} last reported a location {age_h:.1f} hours ago.", device_id=dev["id"])
            elif age_h < self.cfg.stale_hours and flagged:
                db.set_setting(key, False)


# ---------------------------------------------------------------- daily summary
CARRIED = ("iPhone", "Watch", "iPad")


def primary_device(owner: str, t0: float, t1: float) -> dict | None:
    """The device that best represents where a person is: the iPhone with most fixes, else Watch/iPad."""
    best = None
    for rank, cls in enumerate(CARRIED):
        rows = db.q("SELECT d.*, (SELECT COUNT(*) FROM points p WHERE p.device_id=d.id AND p.ts BETWEEN ? AND ?) n "
                    "FROM devices d WHERE d.owner=? AND d.source='icloud' AND d.device_class LIKE ? AND d.hidden=0 "
                    "ORDER BY n DESC", (t0, t1, owner, f"%{cls}%"))
        if rows and rows[0]["n"]:
            return rows[0]
        if rows and best is None:
            best = rows[0]
    return best


def build_digest(cfg: Config, now: float | None = None) -> tuple[str, str]:
    from . import analysis
    now = now or time.time()
    t0 = now - 86400
    owners = [r["owner"] for r in db.q("SELECT DISTINCT owner FROM devices WHERE source='icloud' AND owner IS NOT NULL "
                                       "AND hidden=0 ORDER BY owner")]
    lines = [f"Last 24 hours, to {time.strftime('%H:%M %a %d %b', time.localtime(now))}", ""]
    for owner in owners:
        dev = primary_device(owner, t0, now)
        if dev is None:
            continue
        ev = db.q("SELECT e.ts, e.kind, g.name FROM events e JOIN devices d ON d.id=e.device_id "
                  "JOIN geofences g ON g.id=e.geofence_id WHERE d.owner=? AND e.kind IN ('enter','exit') "
                  "AND e.ts>=? ORDER BY e.ts", (owner, t0))
        moves, last = [], None
        for e in ev:  # collapse phone+watch duplicates
            key = (e["kind"], e["name"])
            if last and key == last[0] and e["ts"] - last[1] < cfg.person_dedupe_minutes * 60:
                continue
            last = (key, e["ts"])
            verb = "arrived at" if e["kind"] == "enter" else "left"
            moves.append(f"    {time.strftime('%H:%M', time.localtime(e['ts']))}  {verb} {e['name']}")
        s = analysis.summary(dev["id"], t0, now, cfg.stay_radius_m, cfg.stay_min_minutes)
        inside = db.q("SELECT g.name, fs.since FROM fence_state fs JOIN geofences g ON g.id=fs.geofence_id "
                      "WHERE fs.device_id=? AND fs.inside=1", (dev["id"],))
        last_seen = dev["last_seen"]
        status = (f"at {inside[0]['name']} since {time.strftime('%H:%M', time.localtime(inside[0]['since']))}"
                  if inside else f"last fix {time.strftime('%H:%M %a', time.localtime(last_seen))}" if last_seen
                  else "no location")
        lines.append(f"{db.person_display(owner)}: {status}. {s['km']} km, {len(s['trips'])} trips ({dev['name']})")
        lines += moves or ["    no arrivals or departures at saved places"]
        lines.append("")
    return f"Daily summary {time.strftime('%a %d %b', time.localtime(now))}", "\n".join(lines)
