"""Nominatim search / reverse geocoding with a local cache and the 1 req/s usage-policy limit.

Only what the user types in the search box, or an explicit "what's here?" click, is sent.
No device positions are ever sent automatically.
"""

from __future__ import annotations

import json
import threading
import time

import requests

from . import db

UA = "family-locator/0.1 (self-hosted)"
_lock = threading.Lock()
_last = 0.0
CACHE_S = 30 * 86400


def _get(base: str, path: str, params: dict) -> list | dict:
    global _last
    key = path + "?" + json.dumps(params, sort_keys=True)
    hit = db.q1("SELECT result, ts FROM geocode_cache WHERE q=?", (key,))
    if hit and time.time() - hit["ts"] < CACHE_S:
        return json.loads(hit["result"])
    with _lock:
        wait = 1.1 - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        r = requests.get(base.rstrip("/") + path, params={**params, "format": "jsonv2"},
                         headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=10)
        _last = time.time()
    r.raise_for_status()
    data = r.json()
    db.x("INSERT OR REPLACE INTO geocode_cache(q,ts,result) VALUES(?,?,?)", (key, time.time(), json.dumps(data)))
    return data


def search(base: str, q: str, near: tuple[float, float] | None = None) -> list[dict]:
    params = {"q": q, "limit": 8, "addressdetails": 0}
    if near:
        lat, lon = near
        params["viewbox"] = f"{lon - 1},{lat + 1},{lon + 1},{lat - 1}"  # bias, not bound
    res = _get(base, "/search", params)
    return [{"name": r.get("display_name"), "lat": float(r["lat"]), "lon": float(r["lon"]),
             "type": r.get("type"), "bbox": [float(x) for x in r.get("boundingbox", [])] or None}
            for r in res]


def reverse(base: str, lat: float, lon: float) -> dict:
    r = _get(base, "/reverse", {"lat": round(lat, 5), "lon": round(lon, 5), "zoom": 18})
    return {"name": r.get("display_name"), "lat": lat, "lon": lon} if isinstance(r, dict) else {}
