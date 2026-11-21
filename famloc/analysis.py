"""Stays, trips and summary statistics, computed on the fly from stored points."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from . import db
from .alerts import haversine_m

BAD_ACC_M = 1000.0


def points(device_id: str, t0: float, t1: float, max_acc: float | None = BAD_ACC_M) -> list[dict]:
    sql = "SELECT ts,lat,lon,acc FROM points WHERE device_id=? AND ts BETWEEN ? AND ?"
    args: list = [device_id, t0, t1]
    if max_acc:
        sql += " AND (acc IS NULL OR acc<=?)"
        args.append(max_acc)
    return db.q(sql + " ORDER BY ts", args)


def stays(pts: list[dict], radius_m: float, min_minutes: float) -> list[dict]:
    """Greedy cluster: grow while points stay within radius of the running centroid."""
    out: list[dict] = []
    i, n = 0, len(pts)
    while i < n:
        clat, clon, k = pts[i]["lat"], pts[i]["lon"], 1
        j = i + 1
        while j < n and haversine_m(clat, clon, pts[j]["lat"], pts[j]["lon"]) <= radius_m:
            clat = (clat * k + pts[j]["lat"]) / (k + 1)
            clon = (clon * k + pts[j]["lon"]) / (k + 1)
            k += 1
            j += 1
        start, end = pts[i]["ts"], pts[j - 1]["ts"]
        if j - i >= 2 and (end - start) >= min_minutes * 60:
            out.append({"lat": clat, "lon": clon, "start": start, "end": end, "n": j - i,
                        "minutes": (end - start) / 60})
            i = j
        else:
            i += 1
    # merge consecutive stays at the same spot split by one stray fix
    merged: list[dict] = []
    for s in out:
        if merged and haversine_m(merged[-1]["lat"], merged[-1]["lon"], s["lat"], s["lon"]) <= radius_m:
            m = merged[-1]
            m["end"], m["n"] = s["end"], m["n"] + s["n"]
            m["minutes"] = (m["end"] - m["start"]) / 60
        else:
            merged.append(s)
    return merged


def path_km(pts: list[dict]) -> float:
    return sum(haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in zip(pts, pts[1:])) / 1000


def trips(pts: list[dict], st: list[dict]) -> list[dict]:
    out = []
    for a, b in zip(st, st[1:]):
        seg = [p for p in pts if a["end"] <= p["ts"] <= b["start"]]
        km = path_km(seg) if len(seg) >= 2 else haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) / 1000
        hours = (b["start"] - a["end"]) / 3600
        out.append({"start": a["end"], "end": b["start"], "from": [a["lat"], a["lon"]],
                    "to": [b["lat"], b["lon"]], "km": round(km, 2),
                    "straight_km": round(haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) / 1000, 2),
                    "minutes": round(hours * 60, 1),
                    "avg_kmh": round(km / hours, 1) if hours > 0 else None})
    return out


def label_stays(st: list[dict]) -> None:
    """Name a stay after the geofence it falls in, if any."""
    fences = db.q("SELECT id,name,lat,lon,radius FROM geofences")
    for s in st:
        for g in fences:
            if haversine_m(s["lat"], s["lon"], g["lat"], g["lon"]) <= g["radius"]:
                s["place"] = g["name"]
                break


def summary(device_id: str, t0: float, t1: float, radius_m: float, min_minutes: float) -> dict:
    pts = points(device_id, t0, t1)
    st = stays(pts, radius_m, min_minutes)
    label_stays(st)
    tr = trips(pts, st)
    per_day: dict[str, float] = defaultdict(float)
    for a, b in zip(pts, pts[1:]):
        day = datetime.fromtimestamp(a["ts"]).strftime("%Y-%m-%d")
        per_day[day] += haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) / 1000
    places: dict[str, dict] = {}
    for s in st:
        key = s.get("place") or f"{s['lat']:.3f},{s['lon']:.3f}"
        p = places.setdefault(key, {"name": key, "lat": s["lat"], "lon": s["lon"], "visits": 0, "hours": 0.0})
        p["visits"] += 1
        p["hours"] += s["minutes"] / 60
    far = max((haversine_m(pts[0]["lat"], pts[0]["lon"], p["lat"], p["lon"]) for p in pts), default=0)
    return {
        "device_id": device_id, "points": len(pts), "km": round(path_km(pts), 1),
        "stays": st, "trips": tr,
        "per_day_km": [{"day": d, "km": round(v, 2)} for d, v in sorted(per_day.items())],
        "top_places": sorted(places.values(), key=lambda p: -p["hours"])[:15],
        "farthest_from_start_km": round(far / 1000, 1),
    }
