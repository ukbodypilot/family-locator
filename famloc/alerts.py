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
        if email and self.email_configured():
            # Send off-thread: SMTP must never stall a poller.
            def _send():
                err = self.send_email(subject, message)
                db.x("UPDATE events SET emailed=?, email_error=? WHERE id=?", (0 if err else 1, err, eid))
            threading.Thread(target=_send, daemon=True, name="email").start()

    # ---------- per-fix checks ----------
    def check_fix(self, dev: dict, ts: float, lat: float, lon: float, acc: float | None) -> None:
        """Run geofence logic for a NEW fix. Only fixes newer than the fence state count."""
        if acc is not None and acc > self.cfg.max_alert_accuracy_m:
            return
        acc = acc or 0.0
        for g in db.q("SELECT * FROM geofences"):
            if g["devices"]:
                allowed = json.loads(g["devices"])
                if allowed and dev["id"] not in allowed:
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
            who = f"{dev['name']}" + (f" ({dev['owner']})" if dev.get("owner") else "")
            when = time.strftime("%H:%M %a %d %b", time.localtime(ts))
            maps = f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=16/{lat:.5f}/{lon:.5f}"
            if inside_now and not st["inside"]:
                db.x("UPDATE fence_state SET inside=1, since=? WHERE geofence_id=? AND device_id=?",
                     (ts, g["id"], dev["id"]))
                if g["on_enter"]:
                    self.notify("enter", f"{who} arrived at {g['name']}",
                                f"{who} arrived at {g['name']} at {when}.\n{maps}",
                                device_id=dev["id"], geofence_id=g["id"], ts=ts)
            elif outside_now and st["inside"]:
                db.x("UPDATE fence_state SET inside=0, since=? WHERE geofence_id=? AND device_id=?",
                     (ts, g["id"], dev["id"]))
                if g["on_exit"]:
                    self.notify("exit", f"{who} left {g['name']}",
                                f"{who} left {g['name']} at {when} (now {d:.0f} m away).\n{maps}",
                                device_id=dev["id"], geofence_id=g["id"], ts=ts)

    def check_battery(self, dev: dict, battery: float | None) -> None:
        if battery is None or battery <= 0 or self.cfg.low_battery_pct <= 0:
            return
        pct = battery * 100
        key = f"lowbatt:{dev['id']}"
        flagged = db.get_setting(key, False)
        if pct <= self.cfg.low_battery_pct and not flagged:
            db.set_setting(key, True)
            who = dev["name"] + (f" ({dev['owner']})" if dev.get("owner") else "")
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
            key = f"stale:{dev['id']}"
            flagged = db.get_setting(key, False)
            age_h = (now - dev["last_seen"]) / 3600
            if age_h >= self.cfg.stale_hours and not flagged:
                db.set_setting(key, True)
                who = dev["name"] + (f" ({dev['owner']})" if dev.get("owner") else "")
                self.notify("stale", f"{who} has not reported for {age_h:.0f} h",
                            f"{who} last reported a location {age_h:.1f} hours ago.", device_id=dev["id"])
            elif age_h < self.cfg.stale_hours and flagged:
                db.set_setting(key, False)
