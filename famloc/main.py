"""Entry point: start the pollers and the web server."""

from __future__ import annotations

import logging
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
    while not stop.wait(600):
        try:
            alerter.check_stale()
        except Exception:  # noqa: BLE001
            logging.getLogger("famloc").exception("housekeeping")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pyicloud").setLevel(logging.WARNING)
    cfg = config.load()
    db.init()
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
