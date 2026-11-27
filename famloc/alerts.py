"""Alerting: geofence enter/exit, low battery, stale devices, login problems. Email via SMTP."""

from __future__ import annotations

import json
from urllib.parse import quote
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


_tunnel_cache: dict = {"ts": 0.0, "url": None}


def lan_url(cfg: Config) -> str:
    """Direct link on the home network."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # UDP "connect" sends nothing; just picks the LAN interface
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        ip = "localhost"
    return f"http://{ip}:{cfg.port}/"


def public_url(cfg: Config) -> str | None:
    """Link that works from anywhere: config override, else the gateway's Cloudflare quick tunnel
    (its hostname changes whenever cloudflared restarts, so ask cloudflared each time, cached 60 s)."""
    if cfg.public_url:
        return cfg.public_url.rstrip("/") + "/"
    now = time.time()
    if now - _tunnel_cache["ts"] < 60:
        return _tunnel_cache["url"]
    url = None
    try:
        import requests
        host = requests.get(cfg.tunnel_metrics_url, timeout=3).json().get("hostname")
        if host:
            url = f"https://{host}{cfg.proxy_prefix}"
    except Exception as e:  # noqa: BLE001 - tunnel down: emails just carry the LAN link
        log.info("no tunnel hostname: %s", e)
    _tunnel_cache.update(ts=now, url=url)
    return url


def viewer_link(cfg: Config, v: dict, fragment: str = "") -> str | None:
    """A user page's link that works from anywhere, or None while the tunnel is down. User pages get
    no home-Wi-Fi link: they are for people who aren't on it."""
    pub = public_url(cfg)
    return f"{pub}v/{v['token']}/" + (f"#{fragment}" if fragment else "") if pub else None


def viewer_links(cfg: Config, v: dict, fragment: str = "") -> str:
    link = viewer_link(cfg, v, fragment)
    return f"Open: {link}" if link else "The link isn't available right now; tomorrow's email will have a fresh one."


def short_link(cfg: Config, pub: str | None) -> str | None:
    """The radio gateway's short link to this page (short_links_file), only while it points at the
    tunnel we see now: after a tunnel change the gateway rewrites the file, and until then the old
    link would lead to a dead hostname. User pages never get one: their links carry a secret token."""
    if not (cfg.short_links_file and pub):
        return None
    try:
        with open(cfg.short_links_file) as f:
            d = json.load(f)
        tunnel = str(d.get("tunnel") or "").rstrip("/")
        link = (d.get("links") or {}).get(cfg.proxy_prefix)
    except (OSError, ValueError, AttributeError):
        return None
    if not tunnel or not pub.startswith(tunnel + "/"):
        return None
    return link if isinstance(link, str) and link.startswith("https://") else None


def links(cfg: Config, fragment: str = "") -> str:
    """Footer lines for emails: the anywhere link (via the gateway's tunnel) and the home-Wi-Fi link."""
    frag = f"#{fragment}" if fragment else ""
    out = []
    pub = public_url(cfg)
    if pub:
        out.append(f"Open (phone, anywhere): {pub}{frag}")
        short = short_link(cfg, pub)
        if short:
            out.append(f"Short link (type it in): {short}")
    out.append(f"Open (home Wi-Fi): {lan_url(cfg)}{frag}")
    return "\n".join(out)


CONFIRM_ROUGH_S = 10 * 60   # rough fixes alone confirm a change after agreeing this long
CONFIRM_MAX_S = 30 * 60     # an unconfirmed change is forgotten after this


class Alerter:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._pending: dict[tuple, tuple[str, float]] = {}   # (fence, device) -> (kind, first ts)

    # ---------- email ----------
    def smtp_ready(self) -> bool:
        """The sending account is set up (user pages need only this; the main page also needs a recipient)."""
        c = self.cfg
        return bool(c.email_enabled and c.email_address and c.email_app_password)

    def email_configured(self) -> bool:
        return bool(self.smtp_ready() and self.cfg.email_recipient)

    def send_email(self, subject: str, body: str, viewer: dict | None = None) -> str | None:
        """Returns None on success, else the error text. With viewer: to that user page's address, with
        only its own link (never the main page's links)."""
        c = self.cfg
        if viewer:
            if not self.smtp_ready():
                return "email not configured"
            if not viewer.get("email"):
                return "no email address for this user page"
            to = viewer["email"]
            if f"v/{viewer['token']}/" not in body and "link isn't available" not in body:
                body += "\n\n" + viewer_links(c, viewer)
        else:
            if not self.email_configured():
                return "email not configured"
            to = c.email_recipient
            if "Open (home Wi-Fi)" not in body:
                body += "\n\n" + links(c)
        msg = MIMEText(body + "\n\n-- Family Locator\n", "plain", "utf-8")
        msg["From"] = c.email_address
        msg["To"] = to
        msg["Subject"] = f"[Family Locator] {subject}"
        try:
            with _email_lock, smtplib.SMTP_SSL(c.smtp_host, c.smtp_port, timeout=20) as s:
                s.login(c.email_address, c.email_app_password)
                s.sendmail(c.email_address, [r.strip() for r in to.split(",")], msg.as_string())
            COUNTERS["emails_sent"] += 1
            return None
        except Exception as e:  # noqa: BLE001 - report every failure, never crash a poller
            COUNTERS["emails_failed"] += 1
            log.warning("email failed: %s", e)
            return str(e)

    def notify(self, kind: str, subject: str, message: str, *, device_id=None, geofence_id=None,
               ts=None, email=True, viewer: dict | None = None) -> None:
        eid = db.add_event(kind, message, device_id=device_id, geofence_id=geofence_id, ts=ts,
                           viewer_id=viewer["id"] if viewer else None)
        COUNTERS["events"] += 1
        log.info("event %s%s: %s", kind, f" (user page {viewer['name']})" if viewer else "", message.split("\n")[0])
        if email and not viewer and kind != "auth" and self.in_quiet_hours():   # quiet hours are the main page's
            db.x("UPDATE events SET email_error='quiet hours' WHERE id=?", (eid,))
            return
        if email and (self.smtp_ready() and viewer.get("email") if viewer else self.email_configured()):
            # Send off-thread: SMTP must never stall a poller.
            def _send():
                err = self.send_email(subject, message, viewer=viewer)
                db.x("UPDATE events SET emailed=?, email_error=? WHERE id=?", (0 if err else 1, err, eid))
            threading.Thread(target=_send, daemon=True, name="email").start()

    def in_quiet_hours(self, now: float | None = None) -> bool:
        a, b = self.cfg.quiet_from_hour, self.cfg.quiet_to_hour
        if a < 0 or b < 0 or a == b:
            return False
        h = time.localtime(now or time.time()).tm_hour
        return a <= h < b if a < b else (h >= a or h < b)

    def _person_already_alerted(self, owner: str | None, geofence_id: int, kind: str, ts: float,
                                dev_id: str | None = None, prev_since: float | None = None) -> bool:
        """Phone and watch cross a fence together; alert once per person. A device that was asleep
        (a laptop in a bag) reports the crossing late: if the person's other devices already said
        so since this device last changed state, it is old news, however late it comes."""
        if not owner:
            return False
        win = self.cfg.person_dedupe_minutes * 60
        r = db.q1("SELECT e.id FROM events e JOIN devices d ON d.id=e.device_id WHERE e.geofence_id=? "
                  "AND e.kind=? AND d.owner=? AND ABS(e.ts-?)<=?", (geofence_id, kind, owner, ts, win))
        if r is not None:
            return True
        if dev_id is None or prev_since is None:
            return False
        last = db.q1("SELECT e.kind FROM events e JOIN devices d ON d.id=e.device_id WHERE e.geofence_id=? "
                     "AND d.owner=? AND e.device_id<>? AND e.kind IN ('enter','exit') AND e.ts>=? AND e.ts<=? "
                     "ORDER BY e.ts DESC LIMIT 1", (geofence_id, owner, dev_id, prev_since, ts))
        return last is not None and last["kind"] == kind

    # ---------- per-fix checks ----------
    def check_fix(self, dev: dict, ts: float, lat: float, lon: float, acc: float | None) -> None:
        """Run geofence logic for a NEW fix. Only fixes newer than the fence state count."""
        if acc is not None and acc > self.cfg.max_alert_accuracy_m:
            return
        acc = acc or 0.0
        viewers: dict = {}
        # fences this device is inside first: one fix that crosses two fences says "left" before "arrived"
        for g in db.q("SELECT g.* FROM geofences g LEFT JOIN fence_state fs ON fs.geofence_id=g.id AND fs.device_id=? "
                      "ORDER BY COALESCE(fs.inside, 0) DESC, g.id", (dev["id"],)):
            people = json.loads(g["people"]) if g.get("people") else []
            devs = json.loads(g["devices"]) if g["devices"] else []
            if (people or devs) and dev.get("owner") not in people and dev["id"] not in devs:
                continue
            v = None
            if g.get("viewer_id"):   # a user page's place: only that page's people, and its own alerts
                if g["viewer_id"] not in viewers:
                    viewers[g["viewer_id"]] = db.viewer(g["viewer_id"])
                v = viewers[g["viewer_id"]]
                if not v or dev.get("owner") not in v["people"] or dev.get("hidden") or dev.get("source") == "photos":
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
            focus = "focus=" + quote(dev.get("owner") or dev["id"])
            when = time.strftime("%H:%M %a %d %b", time.localtime(ts))
            foot = viewer_links(self.cfg, v, focus) if v else links(self.cfg, focus)
            maps = f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=16/{lat:.5f}/{lon:.5f}"
            # A rough fix (cell-tower guesses can land a km away, then snap back) can't flip the state
            # on its own. It waits for a good fix that agrees, or rough fixes that keep agreeing for
            # CONFIRM_ROUGH_S; a fix that contradicts it, or CONFIRM_MAX_S passing, drops it. The
            # alert then carries the time the change was first seen.
            kind = "enter" if inside_now and not st["inside"] else "exit" if outside_now and st["inside"] else None
            key = (g["id"], dev["id"])
            pend = self._pending.get(key)
            if pend and ((pend[0] == "exit" and inside_now) or (pend[0] == "enter" and outside_now)
                         or ts - pend[1] > CONFIRM_MAX_S):
                log.info("dropped unconfirmed %s %s for %s (rough fix %.0f s earlier)", pend[0], g["name"], dev["name"], ts - pend[1])
                self._pending.pop(key, None)
                pend = None
            if kind is None:
                continue
            if acc > self.cfg.rough_accuracy_m:
                if not pend or pend[0] != kind:
                    self._pending[key] = (kind, ts)
                    log.info("rough fix (%.0f m) suggests %s %s for %s: waiting for confirmation", acc, kind, g["name"], dev["name"])
                    continue
                if ts - pend[1] < CONFIRM_ROUGH_S:
                    continue
            if pend and pend[0] == kind:
                ts = pend[1]
                when = time.strftime("%H:%M %a %d %b", time.localtime(ts))
            self._pending.pop(key, None)
            if inside_now and not st["inside"]:
                db.x("UPDATE fence_state SET inside=1, since=? WHERE geofence_id=? AND device_id=?",
                     (ts, g["id"], dev["id"]))
                if g["on_enter"]:
                    dup = self._person_already_alerted(dev.get("owner"), g["id"], "enter", ts, dev["id"], st["since"])
                    self.notify("enter", f"{who} arrived at {g['name']}",
                                f"{who} arrived at {g['name']} at {when}{via}.\n\n{foot}\nMap: {maps}",
                                device_id=dev["id"], geofence_id=g["id"], ts=ts, email=not dup, viewer=v)
            elif outside_now and st["inside"]:
                db.x("UPDATE fence_state SET inside=0, since=? WHERE geofence_id=? AND device_id=?",
                     (ts, g["id"], dev["id"]))
                if g["on_exit"]:
                    dup = self._person_already_alerted(dev.get("owner"), g["id"], "exit", ts, dev["id"], st["since"])
                    self.notify("exit", f"{who} left {g['name']}",
                                f"{who} left {g['name']} at {when}{via} (now {d:.0f} m away).\n\n{foot}\nMap: {maps}",
                                device_id=dev["id"], geofence_id=g["id"], ts=ts, email=not dup, viewer=v)

    def check_battery(self, dev: dict, battery: float | None) -> None:
        if battery is None or battery <= 0 or self.cfg.low_battery_pct <= 0:
            return
        pct = battery * 100
        key = f"lowbatt:{dev['id']}"
        flagged = db.get_setting(key, False)
        if pct <= self.cfg.low_battery_pct and not flagged:
            db.set_setting(key, True)
            who = dev["name"] + (f" ({db.person_display(dev['owner'])})" if dev.get("owner") else "")
            self.notify("battery", f"{who} battery {pct:.0f}%",
                        f"{who} battery is at {pct:.0f}%.\n\n" + links(self.cfg, "focus=" + quote(dev.get("owner") or dev["id"])),
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
                            f"{who} last reported a location {age_h:.1f} hours ago.\n\n"
                            + links(self.cfg, "focus=" + quote(dev.get("owner") or dev["id"])), device_id=dev["id"])
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


def yesterday(now: float) -> tuple[float, float]:
    """Local midnight to midnight of the day before `now` (DST-safe: built from dates, not 86400 s)."""
    lt = time.localtime(now)
    t1 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    y = time.localtime(t1 - 43200)
    return time.mktime((y.tm_year, y.tm_mon, y.tm_mday, 0, 0, 0, 0, 0, -1)), t1


def build_digest(cfg: Config, now: float | None = None, viewer: dict | None = None) -> tuple[str, str]:
    """The main page's summary: the last 24 h, everyone, the main page's places. With viewer: that user
    page's summary of YESTERDAY, its people and its own places only, led by today's link."""
    from . import analysis
    now = now or time.time()
    vid = viewer["id"] if viewer else None
    if viewer:
        t0, t1 = yesterday(now)
        owners = [o for o in viewer["people"] if db.q1("SELECT 1 FROM devices WHERE owner=? AND source='icloud'", (o,))]
        link = viewer_link(cfg, viewer)
        lines = [f"Your link for today: {link}" if link else
                 "Your link isn't available right now; tomorrow's email will have a fresh one.", "",
                 f"Yesterday, {time.strftime('%a %d %b', time.localtime(t0))}", ""]
    else:
        t0, t1 = now - 86400, now
        owners = [r["owner"] for r in db.q("SELECT DISTINCT owner FROM devices WHERE source='icloud' AND owner IS NOT NULL "
                                           "AND hidden=0 ORDER BY owner")]
        lines = [f"Last 24 hours, to {time.strftime('%H:%M %a %d %b', time.localtime(now))}", ""]
    for owner in owners:
        dev = primary_device(owner, t0, t1)
        if dev is None:
            continue
        ev = db.q("SELECT e.ts, e.kind, g.name FROM events e JOIN devices d ON d.id=e.device_id "
                  "JOIN geofences g ON g.id=e.geofence_id WHERE d.owner=? AND e.kind IN ('enter','exit') "
                  "AND g.viewer_id IS ? AND e.ts>=? AND e.ts<? ORDER BY e.ts", (owner, vid, t0, t1))
        moves, last = [], None
        for e in ev:  # collapse phone+watch duplicates
            key = (e["kind"], e["name"])
            if last and key == last[0] and e["ts"] - last[1] < cfg.person_dedupe_minutes * 60:
                continue
            last = (key, e["ts"])
            verb = "arrived at" if e["kind"] == "enter" else "left"
            moves.append(f"    {time.strftime('%H:%M', time.localtime(e['ts']))}  {verb} {e['name']}")
        s = analysis.summary(dev["id"], t0, t1, cfg.stay_radius_m, cfg.stay_min_minutes)
        inside = db.q("SELECT g.name, fs.since FROM fence_state fs JOIN geofences g ON g.id=fs.geofence_id "
                      "WHERE fs.device_id=? AND fs.inside=1 AND g.viewer_id IS ?", (dev["id"], vid))
        last_seen = dev["last_seen"]
        status = (f"now at {inside[0]['name']} since {time.strftime('%H:%M %a', time.localtime(inside[0]['since']))}"
                  if inside else f"last fix {time.strftime('%H:%M %a', time.localtime(last_seen))}" if last_seen
                  else "no location")
        if viewer:
            plink = viewer_link(cfg, viewer, f"focus={quote(owner)}")
            person_link = f"    {plink}" if plink else None
        else:
            pub = public_url(cfg)
            person_link = f"    {(pub or lan_url(cfg))}#focus={quote(owner)}"
        if viewer:
            lines.append(f"{db.person_display(owner)}: {s['km']} km, {len(s['trips'])} trips; {status}.")
        else:   # the main page's summary reads as it always has
            lines.append(f"{db.person_display(owner)}: {status.removeprefix('now ')}. {s['km']} km, "
                         f"{len(s['trips'])} trips ({dev['name']})")
        lines += moves or ["    no arrivals or departures at " + ("your places" if viewer else "saved places")]
        if person_link:
            lines.append(person_link)
        lines.append("")
    if viewer:
        if not owners:
            lines.append("Nobody to show yet.")
        return f"Yesterday: {time.strftime('%a %d %b', time.localtime(t0))}", "\n".join(lines)
    vs = db.q("SELECT id FROM viewers ORDER BY auto_owner IS NULL, name")
    if vs:   # the main summary lists every user page with today's link (the tunnel address changes)
        lines.append("User pages (today's links; each page shows only the people named):")
        lines.append("")
        for r in vs:
            v = db.viewer(r["id"])
            who = ", ".join(db.person_display(o) or o for o in v["people"]) or "nobody yet"
            lines.append(f"{v['name']}'s page" if v.get("auto_owner") else f"{v['name']} (sees {who})")
            lines.append(f"    {viewer_link(cfg, v) or lan_url(cfg) + 'v/' + v['token'] + '/'}")
            lines.append("")
    lines.append(links(cfg, "lanes"))
    return f"Daily summary {time.strftime('%a %d %b', time.localtime(now))}", "\n".join(lines)
