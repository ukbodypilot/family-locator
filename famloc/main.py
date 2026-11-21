"""Entry point: start the pollers and the web server."""

from __future__ import annotations

import logging
import os
import threading
import time

import uvicorn

from . import config, db
from .airtag_source import AirTagSource
from .alerts import Alerter
from .icloud_source import ICloudSource
from .photos_import import PhotoImporter
from .web import create_app


def _housekeeping(alerter: Alerter, stop: threading.Event) -> None:
    log = logging.getLogger("famloc")
    while True:
        try:
            alerter.check_stale()
            last = (db.get_setting("last_backup") or {}).get("ts", 0)
            if time.time() - last > 23 * 3600:
                log.info("backup written: %s", db.backup())
            cfg = alerter.cfg
            today = time.strftime("%Y-%m-%d")
            if (cfg.digest_hour >= 0 and time.localtime().tm_hour == cfg.digest_hour
                    and db.get_setting("last_digest") != today and alerter.email_configured()):
                from .alerts import build_digest
                subj, body = build_digest(cfg)
                err = alerter.send_email(subj, body)
                db.set_setting("last_digest", today)
                db.add_event("info", f"Daily summary emailed" + (f" - FAILED: {err}" if err else ""))
        except Exception:  # noqa: BLE001
            log.exception("housekeeping")
            db.add_event("error", "housekeeping/backup failed - see journal")
        if stop.wait(300):
            return


def main() -> None:
    os.umask(0o077)  # DB, WAL, backups, sessions: owner-only
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pyicloud").setLevel(logging.WARNING)
    cfg = config.load()
    db.init()
    config.apply_overrides(cfg, db.get_setting("cfg_overrides", {}))
    alerter = Alerter(cfg)
    icloud = ICloudSource(cfg, alerter)
    airtag = AirTagSource(cfg, alerter)
    photos = PhotoImporter()
    stop = threading.Event()
    for name, fn in (("icloud", icloud.run_forever), ("airtag", airtag.run_forever)):
        threading.Thread(target=fn, args=(stop,), daemon=True, name=name).start()
    threading.Thread(target=_housekeeping, args=(alerter, stop), daemon=True, name="housekeeping").start()
    db.add_event("info", f"family-locator started", ts=time.time())
    app = create_app(cfg, icloud, airtag, photos, alerter)
    try:
        uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning", access_log=False)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
