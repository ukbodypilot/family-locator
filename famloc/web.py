"""FastAPI app: UI-password auth, JSON API, static map UI."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from collections import defaultdict
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, analysis, config, db, geocode
from .alerts import COUNTERS as ALERT_COUNTERS, lan_url, public_url

log = logging.getLogger("famloc.web")
STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "famloc_session"
SESSION_DAYS = 30
OPEN_PATHS = {"/", "/api/auth/state", "/api/auth/login", "/api/auth/setup", "/metrics", "/favicon.ico", "/api/client-error"}

_fails: dict[str, list[float]] = defaultdict(list)


def _hash(pw: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 300_000).hex()


def _password_set() -> bool:
    return db.get_setting("ui_password") is not None


def _check_password(pw: str) -> bool:
    rec = db.get_setting("ui_password")
    if not rec:
        return False
    return hmac.compare_digest(_hash(pw, bytes.fromhex(rec["salt"])), rec["hash"])


def _new_session(resp: Response) -> None:
    tok = secrets.token_urlsafe(32)
    db.x("INSERT INTO web_sessions(token,expires) VALUES(?,?)", (tok, time.time() + SESSION_DAYS * 86400))
    db.x("DELETE FROM web_sessions WHERE expires<?", (time.time(),))
    # path="/" so the cookie covers every API path, directly on :8790 and under the gateway's /family/
    resp.set_cookie(COOKIE, tok, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict", path="/")


def _authed(req: Request) -> bool:
    tok = req.cookies.get(COOKIE)
    if not tok:
        return False
    r = db.q1("SELECT expires FROM web_sessions WHERE token=?", (tok,))
    return bool(r and r["expires"] > time.time())


def _client_ip(req: Request) -> str:
    """Real client: the gateway proxy (on this box) passes it in X-Forwarded-For; trust only that."""
    host = req.client.host if req.client else "?"
    if host in ("127.0.0.1", "::1"):
        fwd = req.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return fwd
    return host


def _throttled(ip: str) -> bool:
    now = time.time()
    _fails[ip] = [t for t in _fails[ip] if now - t < 300]
    return len(_fails[ip]) >= 5


def create_app(cfg, icloud, airtag, photos, alerter) -> FastAPI:
    app = FastAPI(title="Family Locator", version=__version__, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def auth_mw(request: Request, call_next):
        # The radio-gateway proxy only forwards GET and POST; the UI tunnels PUT/PATCH/DELETE as POST.
        ov = request.headers.get("x-http-method-override", "").upper()
        if request.method == "POST" and ov in ("PUT", "PATCH", "DELETE"):
            request.scope["method"] = ov
        p = request.url.path
        if p in OPEN_PATHS or p.startswith("/static/"):
            return await call_next(request)
        if not _authed(request):
            return JSONResponse({"error": "auth required"}, status_code=401)
        return await call_next(request)

    # ------------------------------------------------------------- auth
    @app.get("/api/auth/state")
    def auth_state(request: Request):
        return {"password_set": _password_set(), "authed": _authed(request), "version": __version__}

    @app.post("/api/auth/setup")
    def auth_setup(response: Response, body: dict = Body(...)):
        if _password_set():
            raise HTTPException(403, "password already set")
        pw = str(body.get("password", ""))
        if len(pw) < 8:
            raise HTTPException(400, "use at least 8 characters")
        salt = secrets.token_bytes(16)
        db.set_setting("ui_password", {"salt": salt.hex(), "hash": _hash(pw, salt)})
        _new_session(response)
        return {"ok": True}

    @app.post("/api/auth/login")
    def auth_login(request: Request, response: Response, body: dict = Body(...)):
        ip = _client_ip(request)
        if _throttled(ip):
            log.warning("[WebAccess] throttled login from %s", ip)
            raise HTTPException(429, "too many attempts, wait 5 minutes")
        if not _check_password(str(body.get("password", ""))):
            _fails[ip].append(time.time())
            log.warning("[WebAccess] failed login from %s", ip)
            raise HTTPException(401, "wrong password")
        log.info("[WebAccess] login from %s", ip)
        _new_session(response)
        return {"ok": True}

    @app.post("/api/auth/logout")
    def auth_logout(request: Request, response: Response):
        db.x("DELETE FROM web_sessions WHERE token=?", (request.cookies.get(COOKIE, ""),))
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.post("/api/auth/change")
    def auth_change(body: dict = Body(...)):
        if not _check_password(str(body.get("old", ""))):
            raise HTTPException(401, "wrong current password")
        pw = str(body.get("new", ""))
        if len(pw) < 8:
            raise HTTPException(400, "use at least 8 characters")
        salt = secrets.token_bytes(16)
        db.set_setting("ui_password", {"salt": salt.hex(), "hash": _hash(pw, salt)})
        return {"ok": True}

    _client_errs: list[float] = []

    @app.post("/api/client-error")
    async def client_error(request: Request):
        """Browser-side script errors, logged so a blank page can be diagnosed. Rate-limited."""
        now = time.time()
        _client_errs[:] = [t for t in _client_errs if now - t < 60]
        if len(_client_errs) >= 20:
            return {"ok": False}
        _client_errs.append(now)
        try:
            body = json.loads((await request.body())[:8000] or b"{}")
        except ValueError:
            body = {}
        log.warning("[ClientError] from %s: %s | %s | %s", _client_ip(request), str(body.get("msg", ""))[:2000],
                    str(body.get("url", ""))[:200], str(body.get("ua", ""))[:200])
        return {"ok": True}

    # ------------------------------------------------------------- data
    @app.get("/api/devices")
    def devices():
        rows = db.q("SELECT d.*, (SELECT COUNT(*) FROM points p WHERE p.device_id=d.id) AS n_points "
                    "FROM devices d ORDER BY d.source, d.owner, d.name")
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
            last = db.q1("SELECT ts,lat,lon,acc,kind FROM points WHERE device_id=? ORDER BY ts DESC LIMIT 1",
                         (r["id"],))
            r["last"] = last
        return rows

    @app.patch("/api/devices/{dev_id:path}")
    def patch_device(dev_id: str, body: dict = Body(...)):
        for k in ("name", "color", "hidden"):
            if k in body:
                db.x(f"UPDATE devices SET {k}=? WHERE id=?", (body[k], dev_id))
        return {"ok": True}

    @app.get("/api/points")
    def get_points(start: float, end: float, devices: str = "", max_acc: float = 0, limit: int = 200000):
        sql = "SELECT device_id,ts,lat,lon,acc,alt,battery,kind,meta FROM points WHERE ts BETWEEN ? AND ?"
        args: list = [start, end]
        ids = [d for d in devices.split(",") if d]
        if ids:
            sql += f" AND device_id IN ({','.join('?' * len(ids))})"
            args += ids
        if max_acc:
            sql += " AND (acc IS NULL OR acc<=?)"
            args.append(max_acc)
        rows = db.q(sql + " ORDER BY ts LIMIT ?", args + [limit])
        out: dict[str, list] = {}
        for r in rows:  # compact columnar-ish form: [ts, lat, lon, acc, battery, kind, meta]
            out.setdefault(r["device_id"], []).append(
                [r["ts"], round(r["lat"], 6), round(r["lon"], 6), r["acc"], r["battery"], r["kind"],
                 json.loads(r["meta"]) if r["meta"] else None])
        return {"start": start, "end": end, "truncated": len(rows) >= limit, "points": out}

    @app.get("/api/range")
    def data_range():
        return db.q1("SELECT MIN(ts) AS first, MAX(ts) AS last, COUNT(*) AS n FROM points")

    @app.get("/api/analysis")
    def get_analysis(device: str, start: float, end: float):
        return analysis.summary(device, start, end, cfg.stay_radius_m, cfg.stay_min_minutes)

    # ------------------------------------------------------------- geofences
    @app.get("/api/geofences")
    def list_fences():
        rows = db.q("SELECT * FROM geofences ORDER BY name")
        for r in rows:
            r["devices"] = json.loads(r["devices"]) if r["devices"] else []
            r["people"] = json.loads(r["people"]) if r.get("people") else []
            r["inside"] = [s["device_id"] for s in
                           db.q("SELECT device_id FROM fence_state WHERE geofence_id=? AND inside=1", (r["id"],))]
        return rows

    def _fence_args(b: dict):
        return (str(b["name"]), float(b["lat"]), float(b["lon"]), max(20.0, float(b.get("radius", 150))),
                b.get("color") or "#9a6b43", int(bool(b.get("on_enter", True))),
                int(bool(b.get("on_exit", True))), json.dumps(b.get("devices") or []),
                json.dumps(b.get("people") or []))

    @app.post("/api/geofences")
    def add_fence(body: dict = Body(...)):
        cur = db.x("INSERT INTO geofences(name,lat,lon,radius,color,on_enter,on_exit,devices,people) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", _fence_args(body))
        return {"id": cur.lastrowid}

    @app.put("/api/geofences/{gid}")
    def put_fence(gid: int, body: dict = Body(...)):
        db.x("UPDATE geofences SET name=?,lat=?,lon=?,radius=?,color=?,on_enter=?,on_exit=?,devices=?,people=? WHERE id=?",
             (*_fence_args(body), gid))
        db.x("DELETE FROM fence_state WHERE geofence_id=?", (gid,))  # re-learn silently after a move
        return {"ok": True}

    @app.delete("/api/geofences/{gid}")
    def del_fence(gid: int):
        db.x("DELETE FROM fence_state WHERE geofence_id=?", (gid,))
        db.x("DELETE FROM geofences WHERE id=?", (gid,))
        return {"ok": True}

    # ------------------------------------------------------------- events / status
    @app.get("/api/events")
    def events(limit: int = 200, kind: str = ""):
        if kind:
            return db.q("SELECT * FROM events WHERE kind=? ORDER BY ts DESC LIMIT ?", (kind, limit))
        return db.q("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))

    @app.get("/api/status")
    def status():
        return {"version": __version__, "icloud": icloud.status(), "airtag": airtag.status(),
                "family_requests": icloud.tracker.family_status(time.time()),
                "photos": photos.status(), "backup": db.get_setting("last_backup"),
                "links": {"public": public_url(cfg), "lan": lan_url(cfg)},
                "tile_keys": {"thunderforest": cfg.thunderforest_key or None},
                "storage": {"db": str(db.DB_PATH), "points": db.q1("SELECT COUNT(*) AS n FROM points")["n"]}, "email": {"configured": alerter.email_configured(),
                                                     "recipient": cfg.email_recipient, **ALERT_COUNTERS},
                "config": {k: getattr(cfg, k) for k in ("poll_seconds", "locate_minutes", "airtag_poll_minutes",
                                                        "low_battery_pct", "stale_hours", "stay_radius_m",
                                                        "stay_min_minutes", "with_family")}}

    @app.get("/metrics")
    def metrics():
        lines = []
        for src, st in (("icloud", icloud.status()), ("airtag", airtag.status())):
            lines.append(f'famloc_source_ok{{source="{src}"}} {1 if st["state"] == "ok" else 0}')
            for k in ("polls", "poll_errors", "new_points"):
                lines.append(f'famloc_{k}_total{{source="{src}"}} {st[k]}')
            lines.append(f'famloc_last_poll_timestamp{{source="{src}"}} {st["last_poll"]}')
        lines.append(f'famloc_single_locates_total {icloud.counters.get("single_locates", 0)}')
        lines.append(f'famloc_session_reinits_total {icloud.counters.get("reinits", 0)}')
        lines.append(f'famloc_sessions_started_total {icloud.counters.get("sessions", 0)}')
        lines.append(f'famloc_session_mode{{mode="{getattr(icloud, "session_mode", "schedule").split(":")[0]}"}} 1')
        lines.append(f"famloc_follows_started_total {icloud.tracker.follows_started}")
        fs = icloud.tracker.family_status(time.time())
        lines.append(f"famloc_family_locates_last_hour {fs['used_last_hour']}")
        lines.append(f"famloc_family_locate_budget {fs['budget']}")
        lines.append(f"famloc_family_paused {1 if fs['paused_until'] else 0}")
        lines.append(f"famloc_family_pause_level {fs['pause_level']}")
        for i, (owner, n) in enumerate(sorted(icloud.tracker.sent_count.items())):
            following = 1 if icloud.tracker.following(owner, time.time()) else 0
            lines.append(f'famloc_person_locates_total{{person="p{i}"}} {n}')
            lines.append(f'famloc_person_following{{person="p{i}"}} {following}')
            lines.append(f'famloc_person_answered_total{{person="p{i}"}} {icloud.tracker.answered.get(owner, 0)}')
            lines.append(f'famloc_person_unanswered_total{{person="p{i}"}} {icloud.tracker.unanswered.get(owner, 0)}')
            lines.append(f'famloc_person_backoff{{person="p{i}"}} {icloud.tracker.backoff_factor(owner)}')
        for k, v in ALERT_COUNTERS.items():
            lines.append(f"famloc_{k}_total {v}")
        n = db.q1("SELECT COUNT(*) AS n FROM points")["n"]
        lines.append(f"famloc_points {n}")
        return PlainTextResponse("\n".join(lines) + "\n")

    # ------------------------------------------------------------- settings
    @app.get("/api/config")
    def get_config():
        ov = db.get_setting("cfg_overrides", {})
        return [{"key": k, "label": lab, "min": lo, "max": hi, "unit": unit, "value": getattr(cfg, k),
                 "overridden": k in ov} for k, (lab, lo, hi, unit) in config.EDITABLE.items()]

    @app.put("/api/config")
    def put_config(body: dict = Body(...)):
        ov = db.get_setting("cfg_overrides", {})
        ov.update(config.apply_overrides(cfg, body))
        db.set_setting("cfg_overrides", ov)
        icloud.poke()
        airtag.poke()
        return get_config()

    # ------------------------------------------------------------- iCloud
    @app.post("/api/icloud/login")
    def icloud_login(body: dict = Body(...)):
        return icloud.login(str(body["apple_id"]).strip(), str(body["password"]),
                            remember=bool(body.get("remember")))

    @app.post("/api/icloud/forget-password")
    def icloud_forget_password():
        return icloud.forget_password()

    @app.post("/api/icloud/2fa")
    def icloud_2fa(body: dict = Body(...)):
        return icloud.submit_2fa(str(body["code"]))

    @app.post("/api/icloud/sms")
    def icloud_sms():
        return icloud.request_sms()

    @app.post("/api/icloud/logout")
    def icloud_logout():
        return icloud.logout()

    @app.post("/api/icloud/poll")
    def icloud_poll(locate: bool = True):
        if icloud.state != "ok":
            raise HTTPException(409, f"iCloud state is {icloud.state}")
        icloud.poll_once(force_locate=locate)
        return icloud.status()

    @app.get("/api/icloud/locate-events")
    def locate_events(since: float = 0, limit: int = 5000):
        rows = db.q("SELECT ts, event, person, data FROM locate_events WHERE ts>=? ORDER BY ts LIMIT ?", (since, limit))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else {}
        return rows

    @app.get("/api/icloud/locate-log")
    def locate_log():
        return getattr(icloud, "locate_log", [])

    @app.post("/api/icloud/locate/{dev_id:path}")
    def icloud_locate_one(dev_id: str):
        if icloud.state != "ok":
            raise HTTPException(409, f"iCloud state is {icloud.state}")
        try:
            return icloud.locate_device(dev_id)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e

    # ------------------------------------------------------------- AirTags
    @app.post("/api/airtag/login")
    def airtag_login(body: dict = Body(...)):
        return airtag.login(str(body["apple_id"]).strip(), str(body["password"]))

    @app.post("/api/airtag/2fa")
    def airtag_2fa(body: dict = Body(...)):
        return airtag.submit_2fa(str(body["code"]))

    @app.post("/api/airtag/logout")
    def airtag_logout():
        return airtag.logout()

    @app.post("/api/airtag/poll")
    def airtag_poll():
        if airtag.state != "ok":
            raise HTTPException(409, f"AirTag state is {airtag.state}")
        airtag.poll_once()
        return airtag.status()

    # ------------------------------------------------------------- photos
    @app.post("/api/photos/icloud")
    def photos_icloud(body: dict = Body(default={})):
        try:
            svc = icloud.photos_service()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(409, str(e)) from e
        return photos.start_icloud(svc, body.get("owner"), int(body.get("limit") or 0))

    @app.post("/api/photos/folder")
    def photos_folder(body: dict = Body(...)):
        return photos.start_folder(str(body["path"]), body.get("owner"))

    @app.delete("/api/photos/{which}")
    def photos_delete(which: str):
        if which not in ("icloud", "folder"):
            raise HTTPException(404)
        db.x("DELETE FROM points WHERE device_id=?", (f"photos:{which}",))
        db.x("DELETE FROM devices WHERE id=?", (f"photos:{which}",))
        return {"ok": True}

    # ------------------------------------------------------------- people
    PERSON_PALETTE = ["#c8385a", "#1f78b4", "#e08e0b", "#7b4fbf", "#0f9d8a", "#b83dba", "#5b6b2e", "#d4622a"]

    @app.get("/api/people")
    def people():
        colors = db.get_setting("person_colors", {})
        names = db.get_setting("person_names", {})
        owners = [r["owner"] for r in db.q("SELECT owner, MIN(first_seen) f FROM devices WHERE owner IS NOT NULL "
                                           "AND source!='photos' GROUP BY owner ORDER BY f")]
        out = []
        for i, o in enumerate(owners):
            out.append({"name": o, "display": names.get(o) or o,
                        "color": colors.get(o) or PERSON_PALETTE[i % len(PERSON_PALETTE)],
                        "tracking": icloud.tracker.status(o, time.time())})
        return out

    @app.patch("/api/people/{name}")
    def patch_person(name: str, body: dict = Body(...)):
        colors = db.get_setting("person_colors", {})
        if body.get("color"):
            colors[name] = str(body["color"])
        db.set_setting("person_colors", colors)
        if "locate_minutes" in body or "follow" in body:
            icloud.tracker.set_person(name, locate_minutes=body.get("locate_minutes", "keep"),
                                      follow=body.get("follow", "keep"))
            icloud.poke()
        if "display" in body:
            names = db.get_setting("person_names", {})
            disp = str(body["display"] or "").strip()[:60]
            if disp and disp != name:
                names[name] = disp
            else:
                names.pop(name, None)  # blank = back to Apple's name
            db.set_setting("person_names", names)
        return {"ok": True}

    @app.post("/api/people/{name}/follow")
    def follow_person(name: str, body: dict = Body(default={})):
        """Follow someone now for N minutes (0 stops following)."""
        icloud.tracker.start_manual(name, float(body.get("minutes", 30)), time.time())
        icloud.poke()
        return icloud.tracker.status(name, time.time())

    @app.get("/api/digest")
    def digest_preview():
        from .alerts import build_digest
        subj, body = build_digest(cfg)
        return {"subject": subj, "body": body}

    @app.post("/api/digest/send")
    def digest_send():
        from .alerts import build_digest
        subj, body = build_digest(cfg)
        err = alerter.send_email(subj, body)
        if err:
            raise HTTPException(502, err)
        return {"ok": True}

    # ------------------------------------------------------------- misc
    @app.post("/api/email/test")
    def email_test():
        err = alerter.send_email("Test email", "This is a test from Family Locator.")
        if err:
            raise HTTPException(502, err)
        return {"ok": True, "to": cfg.email_recipient}

    @app.get("/api/geocode")
    def api_geocode(q: str, lat: float | None = None, lon: float | None = None):
        try:
            return geocode.search(cfg.nominatim_url, q, (lat, lon) if lat is not None and lon is not None else None)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"geocoder: {e}") from e

    @app.get("/api/reverse")
    def api_reverse(lat: float, lon: float):
        try:
            return geocode.reverse(cfg.nominatim_url, lat, lon)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"geocoder: {e}") from e

    @app.get("/")
    def index():
        # Version the app's own JS/CSS URLs by file mtime so a browser can never pair a new page
        # with a stale cached script.
        html = (STATIC / "index.html").read_text()
        for f in ("app.js", "app.css"):
            html = html.replace(f"static/{f}\"", f"static/{f}?v={int((STATIC / f).stat().st_mtime)}\"")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
