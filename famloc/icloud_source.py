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
            from .alerts import lan_url
            self.alerter.notify("auth", "iCloud re-login needed",
                                f"Apple no longer accepts the saved iCloud session ({why}).\n"
                                f"Polling has stopped. Log in again at {lan_url(self.cfg)}/#settings")

    # ------------------------------------------------------------------ polling
    def poke(self) -> None:
        self._wake.set()

    def run_forever(self, stop: threading.Event) -> None:
        self.resume()
        while not stop.is_set():
            if self.state == "ok":
                self.poll_once()
            self._wake.wait(timeout=self.cfg.poll_seconds)
            self._wake.clear()

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
                    # Devices answer a locate over a few seconds; read again shortly after.
                    time.sleep(8)
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

    def _ingest(self, mgr) -> None:
        info = dict(mgr.user_info or {})
        members = info.get("membersInfo") or {}
        me = " ".join(x for x in (info.get("firstName"), info.get("lastName")) if x) or "Me"
        raw_dump = []
        n = 0
        for dev in mgr:
            d = dict(dev.data)
            raw_dump.append(d)
            stable = d.get("deviceDiscoveryId") or d.get("baUUID") or d["id"]
            dev_id = f"icloud:{stable}"
            prs = d.get("prsId")
            m = members.get(prs) if prs else None
            owner = (" ".join(x for x in (m.get("firstName"), m.get("lastName")) if x) if m else me)
            battery = d.get("batteryLevel")
            battery = float(battery) if isinstance(battery, (int, float)) and battery > 0 else None
            db.upsert_device(dev_id, "icloud", d.get("name") or d.get("deviceDisplayName") or "device",
                             owner=owner, model=d.get("deviceDisplayName") or d.get("modelDisplayName"),
                             device_class=d.get("deviceClass"), battery=battery,
                             battery_status=d.get("batteryStatus"),
                             meta={"model": d.get("rawDeviceModel"), "status": d.get("deviceStatus")})
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
        self.counters["devices"] = n
        # Raw response kept for inspection (owner mapping, new fields). Local, 0600.
        p = DATA_DIR / "last_fmip.json"
        p.write_text(json.dumps({"userInfo": info, "content": raw_dump}, indent=1, default=str))
        p.chmod(0o600)

    # ------------------------------------------------------------------ photos
    def photos_service(self):
        with self.lock:
            if self.api is None or self.state != "ok":
                raise RuntimeError("iCloud not logged in")
            return self.api.photos
