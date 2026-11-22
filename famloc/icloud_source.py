"""iCloud Find My (devices + Family Sharing) via pyicloud.

The Apple ID password is NEVER written to disk: it is typed into the web UI, held only inside
the live PyiCloudService object, and gone on restart. What persists is pyicloud's session +
trust cookies in data/icloud_session/ (0700). While those stay valid, a restart resumes with no
password. When Apple expires them, state becomes `need_login` and an email asks for a re-login.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")  # never touch a keyring

from . import db  # noqa: E402
from .alerts import Alerter  # noqa: E402
from .config import DATA_DIR, Config  # noqa: E402

log = logging.getLogger("famloc.icloud")
SESSION_DIR = DATA_DIR / "icloud_session"


class ICloudSource:
    def __init__(self, cfg: Config, alerter: Alerter):
        self.cfg = cfg
        self.alerter = alerter
        self.lock = threading.RLock()
        self.api = None
        self.state = "logged_out"  # logged_out | need_2fa | ok | need_login | error
        self.state_detail = ""
        self.last_poll = 0.0
        self.last_locate = 0.0
        self.last_ok = 0.0
        self.counters = {"polls": 0, "poll_errors": 0, "new_points": 0, "devices": 0}
        self._wake = threading.Event()
        from .tracking import Tracker
        self.tracker = Tracker(cfg)
        self.last_read = 0.0
        self.pending_until = 0.0
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(SESSION_DIR, 0o700)

    # ------------------------------------------------------------------ auth
    @property
    def apple_id(self) -> str | None:
        return db.get_setting("icloud_apple_id")

    def status(self) -> dict:
        return {"state": self.state, "detail": self.state_detail, "apple_id": self.apple_id,
                "last_poll": self.last_poll, "last_ok": self.last_ok, "last_locate": self.last_locate,
                **self.counters}

    def _make(self, apple_id: str, password: str | None):
        from pyicloud import PyiCloudService
        return PyiCloudService(apple_id, password, cookie_directory=str(SESSION_DIR),
                               with_family=self.cfg.with_family, refresh_interval=None,
                               family_poll_delay=1.0, family_poll_max_retries=8)

    def resume(self) -> None:
        """On startup: reuse the saved session without a password, if Apple still accepts it."""
        aid = self.apple_id
        if not aid:
            return
        with self.lock:
            try:
                api = self._make(aid, None)
                if api.requires_2fa or api.requires_2sa:
                    self._set_need_login("saved session needs 2FA again")
                    return
                self.api = api
                self.state, self.state_detail = "ok", "resumed saved session"
                log.info("iCloud session resumed for saved Apple ID")
            except Exception as e:  # noqa: BLE001
                self._set_need_login(f"saved session not accepted: {type(e).__name__}: {e}")

    def login(self, apple_id: str, password: str) -> dict:
        from pyicloud.exceptions import PyiCloudFailedLoginException
        with self.lock:
            try:
                api = self._make(apple_id, password)
            except PyiCloudFailedLoginException as e:
                self.state, self.state_detail = "logged_out", f"login failed: {e}"
                return self.status()
            db.set_setting("icloud_apple_id", apple_id)
            self.api = api
            if api.requires_2fa or api.requires_2sa:
                self.state, self.state_detail = "need_2fa", "enter the code shown on your Apple device"
            else:
                self._logged_in()
            return self.status()

    def request_sms(self) -> dict:
        with self.lock:
            if self.api is None:
                return self.status()
            try:
                self.api.request_2fa_code()
                self.state_detail = "code requested"
            except Exception as e:  # noqa: BLE001
                self.state_detail = f"could not request code: {e}"
            return self.status()

    def submit_2fa(self, code: str) -> dict:
        with self.lock:
            if self.api is None:
                return self.status()
            try:
                ok = self.api.validate_2fa_code(code.strip())
            except Exception as e:  # noqa: BLE001
                ok = False
                self.state_detail = f"code rejected: {e}"
            if not ok:
                self.state_detail = self.state_detail or "code rejected"
                return self.status()
            if not self.api.is_trusted_session:
                try:
                    self.api.trust_session()
                except Exception as e:  # noqa: BLE001
                    log.warning("trust_session failed: %s", e)
            self._logged_in()
            return self.status()

    def logout(self) -> dict:
        with self.lock:
            self.api = None
            for p in SESSION_DIR.iterdir():
                p.unlink()
            self.state, self.state_detail = "logged_out", "session files deleted"
            return self.status()

    def _logged_in(self) -> None:
        self.state, self.state_detail = "ok", "logged in"
        db.set_setting("icloud_need_login_mailed", False)
        self.alerter.notify("auth", "iCloud login OK", "iCloud Find My login succeeded.", email=False)
        self._wake.set()

    def _set_need_login(self, why: str) -> None:
        self.api = None
        self.state, self.state_detail = "need_login", why
        log.warning("iCloud needs login: %s", why)
        if not db.get_setting("icloud_need_login_mailed", False):
            db.set_setting("icloud_need_login_mailed", True)
            from .alerts import links
            self.alerter.notify("auth", "iCloud re-login needed",
                                f"Apple no longer accepts the saved iCloud session ({why}).\n"
                                f"Tracking has stopped until you sign in again on the Setup tab.\n\n"
                                + links(self.cfg, "setup"))

    # ------------------------------------------------------------------ polling
    def poke(self) -> None:
        self._wake.set()

    TICK_S = 15

    def run_forever(self, stop: threading.Event) -> None:
        """Tick every 15 s: send any per-person locates that are due, and read Find My when the
        read interval has passed or a locate is waiting for its answer."""
        self.resume()
        while not stop.is_set():
            if self.state == "ok":
                self.tick()
            self._wake.wait(timeout=self.TICK_S)
            self._wake.clear()

    def _owners(self) -> list[str]:
        return [r["owner"] for r in db.q(
            "SELECT owner, MIN(first_seen) f FROM devices WHERE source='icloud' AND owner IS NOT NULL "
            "AND hidden=0 GROUP BY owner ORDER BY f")]

    def tick(self, now: float | None = None) -> None:
        from pyicloud.exceptions import (PyiCloud2FARequiredException, PyiCloudAuthRequiredException,
                                         PyiCloudFailedLoginException)
        from .alerts import primary_device
        now = now or time.time()
        with self.lock:
            if self.api is None:
                return
            try:
                mgr = self.api.devices
                sent = False
                for owner in self.tracker.check_timeouts(now):
                    st = self.tracker.status(owner, now)
                    if st["unanswered_streak"] == self.tracker.BACKOFF_AFTER:
                        log.warning("no new fix from %s after %d requests in a row; backing off (every %s min)",
                                    owner, st["unanswered_streak"], st["effective_minutes"])
                for owner in self.tracker.due(self._owners(), now):
                    dev = primary_device(owner, now - 86400, now)
                    if dev is None or json.loads(dev["meta"] or "{}").get("locationCapable") is False:
                        self.tracker.mark_sent(owner, now, requested=False)  # nothing locatable
                        continue
                    ok = self._request_locate(mgr, dev["id"], owner)
                    if ok:
                        self.last_locate = now
                        sent = True
                    self.tracker.mark_sent(owner, now, requested=ok)
                if sent:
                    self.pending_until = now + 50  # answers take ~5-30 s
                waiting = self.pending_until > now and now - self.last_read >= 10
                if now - self.last_read >= self.cfg.poll_seconds or waiting:
                    self.last_poll = now
                    self.counters["polls"] += 1
                    mgr.refresh(locate=False)
                    self._ingest(mgr)
                    self.last_read = now
                    self.last_ok = time.time()
                    if self.state_detail.startswith("poll error"):
                        self.state_detail = "polling"
            except (PyiCloudFailedLoginException, PyiCloud2FARequiredException,
                    PyiCloudAuthRequiredException) as e:
                self._set_need_login(f"{type(e).__name__}: {e}")
            except Exception as e:  # noqa: BLE001
                self.counters["poll_errors"] += 1
                self.state_detail = f"poll error: {type(e).__name__}: {e}"
                log.warning("tick error: %s", traceback.format_exc())

    def _request_locate(self, mgr, dev_id: str, owner: str | None = None) -> bool:
        """Send Find My's single-device locate (selectedDevice) and return straight away.
        Apple's reply is summarised in self.locate_log (newest last) for diagnosis."""
        from pyicloud.services.findmyiphone import _FMIP_CLIENT_CONTEXT_TIMEZONE
        target = next((dv for dv in mgr if self._dev_id(dict(dv.data)) == dev_id), None)
        if target is None:
            return False
        req = {"clientContext": {"appName": "iCloud Find (Web)", "appVersion": "2.0", "apiVersion": "3.0",
                                 "deviceListVersion": 1, "fmly": self.cfg.with_family,
                                 "timezone": _FMIP_CLIENT_CONTEXT_TIMEZONE, "inactiveTime": 0,
                                 "shouldLocate": True, "selectedDevice": target.data["id"]},
               "serverContext": mgr._server_ctx}
        with mgr._refresh_lock:
            r = mgr.session.post(mgr._fmip_refresh_url, params=mgr.params, json=req, timeout=20)
            try:
                resp = r.json()
            except ValueError:
                resp = {}
            if resp.get("serverContext"):
                mgr._server_ctx = resp["serverContext"]
                mgr._server_ctx["theftLoss"] = None
        mine = next((c for c in resp.get("content") or [] if c.get("id") == target.data["id"]), {})
        loc = mine.get("location") or {}
        info = {"t": time.time(), "owner": owner, "http": getattr(r, "status_code", None),
                "statusCode": resp.get("statusCode"), "devices_in_reply": len(resp.get("content") or []),
                "isLocating": mine.get("isLocating"), "fix_age_s": round(time.time() - loc["timeStamp"] / 1000) if loc.get("timeStamp") else None,
                "deviceStatus": mine.get("deviceStatus")}
        self.locate_log = (getattr(self, "locate_log", []) + [info])[-40:]
        bad = info["http"] not in (200, None) or (info["statusCode"] not in (None, "200", 200))
        (log.warning if bad else log.info)("locate %s: http=%s statusCode=%s isLocating=%s fix_age=%ss devices_in_reply=%s",
                                           owner, info["http"], info["statusCode"], info["isLocating"],
                                           info["fix_age_s"], info["devices_in_reply"])
        self.counters["single_locates"] = self.counters.get("single_locates", 0) + 1
        return True

    def poll_once(self, force_locate: bool = False) -> None:
        from pyicloud.exceptions import (PyiCloud2FARequiredException, PyiCloudAuthRequiredException,
                                         PyiCloudFailedLoginException)
        with self.lock:
            if self.api is None:
                return
            self.last_poll = time.time()
            self.counters["polls"] += 1
            locate = force_locate or (self.cfg.locate_minutes > 0 and
                                      time.time() - self.last_locate >= self.cfg.locate_minutes * 60)
            try:
                mgr = self.api.devices  # first access does initClient
                mgr.refresh(locate=locate)
                if locate:
                    self.last_locate = time.time()
                    # Devices answer a locate over ~5-30 s; ingest twice so slow ones land too.
                    time.sleep(8)
                    mgr.refresh(locate=False)
                    self._ingest(mgr)
                    time.sleep(12)
                    mgr.refresh(locate=False)
                self._ingest(mgr)
                self.last_ok = time.time()
                if self.state_detail.startswith("poll error"):
                    self.state_detail = "polling"
            except (PyiCloudFailedLoginException, PyiCloud2FARequiredException,
                    PyiCloudAuthRequiredException) as e:
                self._set_need_login(f"{type(e).__name__}: {e}")
            except Exception as e:  # noqa: BLE001
                self.counters["poll_errors"] += 1
                self.state_detail = f"poll error: {type(e).__name__}: {e}"
                log.warning("poll error: %s", traceback.format_exc())

    @staticmethod
    def _dev_id(d: dict) -> str:
        return f"icloud:{d.get('deviceDiscoveryId') or d.get('baUUID') or d['id']}"

    def locate_device(self, dev_id: str) -> dict:
        """Ask ONE device for a fresh fix (Find My's selectedDevice), wait for it, ingest."""
        with self.lock:
            if self.api is None:
                raise KeyError("not logged in")
            mgr = self.api.devices
            target = next((dv for dv in mgr if self._dev_id(dict(dv.data)) == dev_id), None)
            if target is None:
                raise KeyError(f"{dev_id} not in the current Find My device list")
            before = (target.data.get("location") or {}).get("timeStamp")
            self._request_locate(mgr, dev_id)
            after, waited = before, 0
            while waited < 36:
                time.sleep(6)
                waited += 6
                mgr.refresh(locate=False)
                after = (target.data.get("location") or {}).get("timeStamp")
                if after and after != before:
                    break
            self._ingest(mgr)
            self.last_ok = time.time()
            return {"device": dev_id, "updated": bool(after and after != before),
                    "fix_ts": after / 1000 if after else None, "waited_s": waited}

    def _ingest(self, mgr) -> None:
        info = dict(mgr.user_info or {})
        members = info.get("membersInfo") or {}
        me = " ".join(x for x in (info.get("firstName"), info.get("lastName")) if x) or "Me"
        raw_dump = []
        n = 0
        new_owners: set[str] = set()
        for dev in mgr:
            d = dict(dev.data)
            raw_dump.append(d)
            dev_id = self._dev_id(d)
            prs = d.get("prsId")
            m = members.get(prs) if prs else None
            owner = (" ".join(x for x in (m.get("firstName"), m.get("lastName")) if x) if m else me)
            battery = d.get("batteryLevel")
            battery = float(battery) if isinstance(battery, (int, float)) and battery > 0 else None
            db.upsert_device(dev_id, "icloud", d.get("name") or d.get("deviceDisplayName") or "device",
                             owner=owner, model=d.get("deviceDisplayName") or d.get("modelDisplayName"),
                             device_class=d.get("deviceClass"), battery=battery,
                             battery_status=d.get("batteryStatus"),
                             meta={"model": d.get("rawDeviceModel"), "status": d.get("deviceStatus"),
                                   "locationCapable": d.get("locationCapable"),
                                   "lowPowerMode": d.get("lowPowerMode")})
            row = db.q1("SELECT * FROM devices WHERE id=?", (dev_id,))
            n += 1
            self.alerter.check_battery(row, battery)
            loc = d.get("location")
            if not loc or loc.get("latitude") is None or not loc.get("timeStamp"):
                continue
            ts = float(loc["timeStamp"]) / 1000.0
            lat, lon = float(loc["latitude"]), float(loc["longitude"])
            acc = loc.get("horizontalAccuracy")
            if db.add_point(dev_id, ts, lat, lon, acc=acc, alt=loc.get("altitude"), battery=battery,
                            kind=loc.get("positionType"),
                            meta={"isOld": loc.get("isOld"), "inaccurate": loc.get("isInaccurate")}):
                self.counters["new_points"] += 1
                self.alerter.check_fix(row, ts, lat, lon, acc)
                if owner and any(k in (d.get("deviceClass") or "") for k in ("iPhone", "Watch", "iPad")):
                    new_owners.add(owner)
                    self.tracker.note_fix(owner, ts)
        self.counters["devices"] = n
        self._check_movement(new_owners)
        # Raw response kept for inspection (owner mapping, new fields). Local, 0600.
        p = DATA_DIR / "last_fmip.json"
        p.write_text(json.dumps({"userInfo": info, "content": raw_dump}, indent=1, default=str))
        p.chmod(0o600)

    def _check_movement(self, owners: set[str]) -> None:
        now = time.time()
        win = max(self.cfg.follow_rest_minutes * 60, 35 * 60) + 60
        for owner in owners:
            seqs: dict[str, list] = {}
            for r in db.q(
                "SELECT p.device_id, p.ts, p.lat, p.lon, p.acc FROM points p JOIN devices d ON d.id=p.device_id "
                "WHERE d.owner=? AND d.source='icloud' AND d.hidden=0 AND p.ts>=? "
                "AND (d.device_class LIKE '%iPhone%' OR d.device_class LIKE '%Watch%' OR d.device_class LIKE '%iPad%') "
                "ORDER BY p.ts", (owner, now - win)):
                seqs.setdefault(r["device_id"], []).append((r["ts"], r["lat"], r["lon"], r["acc"]))
            was = self.tracker.following(owner, now)
            if self.tracker.on_fixes(owner, list(seqs.values()), now) and not was:
                log.info("following %s while moving", owner)
                self._wake.set()

    # ------------------------------------------------------------------ photos
    def photos_service(self):
        with self.lock:
            if self.api is None or self.state != "ok":
                raise RuntimeError("iCloud not logged in")
            return self.api.photos
