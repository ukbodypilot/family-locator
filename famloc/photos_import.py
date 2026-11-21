"""Backfill history from photo GPS.

Two importers, both write points under a 'photos' pseudo-device so they never trigger alerts:
  * iCloud Photos: reads the CloudKit `locationEnc` metadata per asset - no image download.
  * Local folder: EXIF GPS from JPEG/HEIC files (e.g. a Google Takeout or exported library).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db

log = logging.getLogger("famloc.photos")


class PhotoImporter:
    def __init__(self):
        self.lock = threading.Lock()
        self.job: dict = {"running": False}

    def status(self) -> dict:
        return dict(self.job)

    def _start(self, kind: str, target, *args) -> dict:
        with self.lock:
            if self.job.get("running"):
                return self.status()
            self.job = {"running": True, "kind": kind, "scanned": 0, "with_gps": 0, "new": 0,
                        "started": time.time(), "error": None}

        def wrap():
            try:
                target(*args)
            except Exception as e:  # noqa: BLE001
                log.exception("photo import failed")
                self.job["error"] = f"{type(e).__name__}: {e}"
            finally:
                self.job["running"] = False
                self.job["finished"] = time.time()
                db.add_event("info", f"Photo import ({kind}) finished: {self.job['scanned']} scanned, "
                                     f"{self.job['with_gps']} with GPS, {self.job['new']} new points"
                             + (f", error: {self.job['error']}" if self.job.get("error") else ""))
        threading.Thread(target=wrap, daemon=True, name="photo-import").start()
        return self.status()

    # ---------------------------------------------------------------- iCloud
    def start_icloud(self, photos_service, owner: str | None, limit: int = 0) -> dict:
        return self._start("icloud", self._run_icloud, photos_service, owner, limit)

    def _run_icloud(self, photos, owner, limit):
        from pyicloud.services.photos.materialize import _extract_location
        dev_id = "photos:icloud"
        db.upsert_device(dev_id, "photos", "iCloud Photos", owner=owner, model="Photo GPS",
                         device_class="Photos")
        for asset in photos.all:
            self.job["scanned"] += 1
            if limit and self.job["scanned"] > limit:
                break
            loc = _extract_location(asset.asset_record)
            lat, lon = loc.get("latitude"), loc.get("longitude")
            if lat is None or lon is None or (lat == 0 and lon == 0):
                continue
            self.job["with_gps"] += 1
            ts = loc.get("timestamp") or asset.asset_date
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts = ts.timestamp()
            if db.add_point(dev_id, float(ts), lat, lon, alt=loc.get("altitude"), kind="photo",
                            meta={"file": asset.filename, "asset": asset.id}):
                self.job["new"] += 1

    # ---------------------------------------------------------------- folder
    def start_folder(self, folder: str, owner: str | None) -> dict:
        p = Path(folder).expanduser()
        if not p.is_dir():
            return {"running": False, "error": f"not a directory: {p}"}
        return self._start("folder", self._run_folder, p, owner)

    def _run_folder(self, folder: Path, owner):
        from PIL import Image
        try:
            from pillow_heif import register_heif_opener  # optional HEIC support
            register_heif_opener()
        except ImportError:
            pass
        dev_id = "photos:folder"
        db.upsert_device(dev_id, "photos", "Photo folder", owner=owner, model="Photo GPS",
                         device_class="Photos")
        exts = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff", ".dng"}
        for f in folder.rglob("*"):
            if f.suffix.lower() not in exts:
                continue
            self.job["scanned"] += 1
            try:
                with Image.open(f) as im:
                    exif = im.getexif()
                gps = exif.get_ifd(0x8825)
                if not gps or 2 not in gps or 4 not in gps:
                    continue
                lat = _dms(gps[2], gps.get(1, "N"))
                lon = _dms(gps[4], gps.get(3, "E"))
                ts = _exif_time(exif, gps)
                if ts is None:
                    ts = f.stat().st_mtime
                self.job["with_gps"] += 1
                alt = float(gps[6]) if 6 in gps else None
                if db.add_point(dev_id, ts, lat, lon, alt=alt, kind="photo", meta={"file": str(f)}):
                    self.job["new"] += 1
            except Exception as e:  # noqa: BLE001
                log.debug("skip %s: %s", f, e)


def _dms(v, ref) -> float:
    d, m, s = (float(x) for x in v)
    val = d + m / 60 + s / 3600
    return -val if str(ref).upper() in ("S", "W") else val


def _exif_time(exif, gps) -> float | None:
    # Prefer GPS UTC date+time; else DateTimeOriginal (local time, offset if recorded).
    try:
        if 29 in gps and 7 in gps:
            h, m, s = (float(x) for x in gps[7])
            dt = datetime.strptime(str(gps[29]), "%Y:%m:%d").replace(tzinfo=timezone.utc)
            return dt.timestamp() + h * 3600 + m * 60 + s
    except (ValueError, TypeError):
        pass
    ifd = exif.get_ifd(0x8769)
    raw = ifd.get(36867) or exif.get(306)
    if not raw:
        return None
    try:
        dt = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    off = ifd.get(36881)  # OffsetTimeOriginal "+01:00"
    if off:
        try:
            return datetime.strptime(f"{raw} {off}", "%Y:%m:%d %H:%M:%S %z").timestamp()
        except ValueError:
            pass
    return dt.timestamp()  # assume this machine's local zone
