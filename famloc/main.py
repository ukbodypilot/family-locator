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


def _viewer_digests(alerter: Alerter) -> None:
    """User pages with the daily email on get yesterday's summary and today's link at their hour."""
    from .alerts import build_digest
    today, hour = time.strftime("%Y-%m-%d"), time.localtime().tm_hour
    for r in db.q("SELECT id FROM viewers WHERE digest=1 AND email IS NOT NULL AND email!='' "
                  "AND digest_hour=? AND (last_digest IS NULL OR last_digest!=?)", (hour, today)):
        if not alerter.smtp_ready():
            return
        v = db.viewer(r["id"])
        subj, body = build_digest(alerter.cfg, viewer=v)
        err = alerter.send_email(subj, body, viewer=v)
        db.x("UPDATE viewers SET last_digest=? WHERE id=?", (today, v["id"]))
        db.add_event("info", f"Daily summary emailed to {v['name']}'s page" + (f" - FAILED: {err}" if err else ""),
                     viewer_id=v["id"])


def _housekeeping(alerter: Alerter, stop: threading.Event) -> None:
    log = logging.getLogger("famloc")
    while True:
        try:
            alerter.check_stale()
            db.x("DELETE FROM locate_events WHERE ts < ?", (time.time() - 30 * 86400,))
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
            from .web import new_token
            db.ensure_person_pages(new_token)
            _viewer_digests(alerter)
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
    db.add_event("info", "Family Locator started", ts=time.time())
    app = create_app(cfg, icloud, airtag, photos, alerter)
    try:
        uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning", access_log=False)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
