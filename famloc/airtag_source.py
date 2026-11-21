"""AirTags / Find My items via FindMy.py.

Needs two things:
  1. Accessory key files in data/airtags/ - the JSON files that `python -m findmy decrypt
     --out-dir <dir>` writes ON A MAC (or the raw .plist files from the Find My cache).
  2. A FindMy.py Apple login (separate from pyicloud's; same Apple ID is fine).

FindMy.py's saved account state normally includes the password. We blank it before saving, so
nothing on disk can log in by itself; when Apple's tokens expire you log in again from the UI.
Every FindMy.py call runs on ONE worker thread because its sync wrapper owns an asyncio loop.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db
from .alerts import Alerter
from .config import DATA_DIR, Config

log = logging.getLogger("famloc.airtag")
TAG_DIR = DATA_DIR / "airtags"
STATE_PATH = DATA_DIR / "findmy_account.json"
LIBS_PATH = DATA_DIR / "anisette_libs.bin"


class AirTagSource:
    def __init__(self, cfg: Config, alerter: Alerter):
        self.cfg = cfg
        self.alerter = alerter
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="findmy")
        self.account = None
        self._2fa = None
        self.state = "logged_out"  # logged_out | need_2fa | ok | need_login | error | no_keys
        self.state_detail = ""
        self.last_poll = 0.0
        self.counters = {"polls": 0, "poll_errors": 0, "new_points": 0, "tags": 0}
        self._wake = threading.Event()
        TAG_DIR.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict:
        return {"state": self.state, "detail": self.state_detail, "last_poll": self.last_poll,
                "key_files": sorted(p.name for p in self._key_files()), "tag_dir": str(TAG_DIR),
                **self.counters}

    def _key_files(self) -> list[Path]:
        return [p for p in TAG_DIR.iterdir() if p.suffix.lower() in (".json", ".plist")]

    def _run(self, fn, *a, timeout=180):
        return self.pool.submit(fn, *a).result(timeout=timeout)

    # ------------------------------------------------------------------ auth
    def _save_state(self) -> None:
        st = self.account.to_json()
        st = json.loads(json.dumps(st, default=str))
        if isinstance(st.get("account"), dict):
            st["account"]["password"] = None  # never persist the password
        STATE_PATH.write_text(json.dumps(st))
        STATE_PATH.chmod(0o600)

    def resume(self) -> None:
        if not STATE_PATH.exists():
            return

        def _do():
            from findmy import AppleAccount, LoginState
            self.account = AppleAccount.from_json(str(STATE_PATH), anisette_libs_path=str(LIBS_PATH))
            if self.account.login_state == LoginState.LOGGED_IN:
                self.state, self.state_detail = "ok", "resumed saved session"
            else:
                self.state, self.state_detail = "need_login", f"saved state is {self.account.login_state.name}"
        try:
            self._run(_do)
        except Exception as e:  # noqa: BLE001
            self.state, self.state_detail = "need_login", f"resume failed: {e}"

    def login(self, apple_id: str, password: str) -> dict:
        def _do():
            from findmy import AppleAccount, LocalAnisetteProvider, LoginState
            from findmy.reports.twofactor import TrustedDeviceSecondFactorMethod
            self.account = AppleAccount(LocalAnisetteProvider(libs_path=str(LIBS_PATH)))
            st = self.account.login(apple_id, password)
            if st == LoginState.REQUIRE_2FA:
                methods = self.account.get_2fa_methods()
                # Prefer the trusted-device push; fall back to the first SMS number.
                m = next((x for x in methods if isinstance(x, TrustedDeviceSecondFactorMethod)), None) \
                    or (methods[0] if methods else None)
                if m is None:
                    raise RuntimeError("Apple offered no 2FA method")
                m.request()
                self._2fa = m
                self.state, self.state_detail = "need_2fa", f"code sent via {type(m).__name__}"
            else:
                self._logged_in()
        try:
            self._run(_do)
        except Exception as e:  # noqa: BLE001
            self.state, self.state_detail = "logged_out", f"login failed: {type(e).__name__}: {e}"
        return self.status()

    def submit_2fa(self, code: str) -> dict:
        def _do():
            from findmy import LoginState
            st = self._2fa.submit(code.strip())
            if st == LoginState.LOGGED_IN:
                self._logged_in()
            else:
                self.state_detail = f"after code: {st.name}"
        if self._2fa is None:
            return self.status()
        try:
            self._run(_do)
        except Exception as e:  # noqa: BLE001
            self.state_detail = f"code rejected: {e}"
        return self.status()

    def _logged_in(self) -> None:
        self._save_state()
        db.set_setting("findmy_need_login_mailed", False)
        self._2fa = None
        self.state, self.state_detail = "ok", "logged in"
        self._wake.set()

    def logout(self) -> dict:
        self.account = None
        STATE_PATH.unlink(missing_ok=True)
        self.state, self.state_detail = "logged_out", "session deleted"
        return self.status()

    # ------------------------------------------------------------------ polling
    def _load_accessories(self):
        from findmy import FindMyAccessory
        accs = []
        for p in self._key_files():
            try:
                if p.suffix.lower() == ".plist":
                    acc = FindMyAccessory.from_plist(str(p), name=p.stem)
                else:
                    acc = FindMyAccessory.from_json(str(p))
                accs.append(acc)
            except Exception as e:  # noqa: BLE001
                log.warning("bad key file %s: %s", p.name, e)
        return accs

    def poke(self) -> None:
        self._wake.set()

    def run_forever(self, stop: threading.Event) -> None:
        self.resume()
        while not stop.is_set():
            if self.state == "ok":
                self.poll_once()
            self._wake.wait(timeout=max(60, self.cfg.airtag_poll_minutes * 60))
            self._wake.clear()

    def poll_once(self) -> None:
        def _do():
            accs = self._load_accessories()
            self.counters["tags"] = len(accs)
            if not accs:
                self.state_detail = f"logged in; no key files in {TAG_DIR}"
                return
            hist = self.account.fetch_location_history(accs)
            for acc, reports in hist.items():
                ident = acc.identifier or acc.name or "tag"
                dev_id = f"airtag:{ident}"
                db.upsert_device(dev_id, "airtag", acc.name or ident, owner=None,
                                 model=getattr(acc, "model", None) or "AirTag", device_class="AirTag")
                row = db.q1("SELECT * FROM devices WHERE id=?", (dev_id,))
                for r in sorted(reports, key=lambda r: r.timestamp):
                    ts = r.timestamp.timestamp()
                    if db.add_point(dev_id, ts, r.latitude, r.longitude, acc=r.horizontal_accuracy,
                                    kind="airtag", meta={"confidence": r.confidence, "status": r.status}):
                        self.counters["new_points"] += 1
                        self.alerter.check_fix(row, ts, r.latitude, r.longitude, r.horizontal_accuracy)
            self._save_state()  # tokens may have been refreshed
            self.state_detail = f"polled {len(accs)} tag(s)"

        self.last_poll = time.time()
        self.counters["polls"] += 1
        try:
            self._run(_do, timeout=300)
        except Exception as e:  # noqa: BLE001
            from findmy import UnauthorizedError
            self.counters["poll_errors"] += 1
            if isinstance(e, UnauthorizedError):
                self.state = "need_login"
                if not db.get_setting("findmy_need_login_mailed", False):
                    db.set_setting("findmy_need_login_mailed", True)
                    self.alerter.notify("auth", "AirTag login needed",
                                        "FindMy.py session expired; log in again on the Settings tab.")
            self.state_detail = f"poll error: {type(e).__name__}: {e}"
            log.warning("airtag poll error: %s", traceback.format_exc())
