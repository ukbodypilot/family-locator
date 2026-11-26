"""FastAPI app: UI-password auth, JSON API, static map UI."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections import defaultdict
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, analysis, config, db, geocode
from .alerts import COUNTERS as ALERT_COUNTERS, lan_url, public_url

log = logging.getLogger("famloc.web")
STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "famloc_session"
SESSION_DAYS = 30
OPEN_PATHS = {"/", "/api/auth/state", "/api/auth/login", "/api/auth/setup", "/metrics", "/favicon.ico", "/api/client-error"}

_fails: dict[str, list[float]] = defaultdict(list)

# ---- user pages (v2): /v/<token>/ is a cut-down copy of the page for someone else, scoped to the
# people chosen for it. The token in the path is the only credential. Requests under it are rewritten
# to the normal /api/... routes with the viewer attached, and only the routes below are reachable:
# everything else (tracking, settings, iCloud, the user list...) answers 403.
VIEWER_PATH = re.compile(r"^/v/([A-Za-z0-9_-]{16,64})(/.*)?$")
VIEWER_ROUTES = [(m, re.compile(p)) for m, p in (
    ("GET", r"^/api/auth/state$"), ("GET", r"^/api/devices$"), ("GET", r"^/api/points$"),
    ("GET", r"^/api/range$"), ("GET", r"^/api/analysis$"), ("GET", r"^/api/people$"),
    ("GET", r"^/api/geofences$"), ("POST", r"^/api/geofences$"), ("PUT", r"^/api/geofences/\d+$"),
    ("DELETE", r"^/api/geofences/\d+$"), ("GET", r"^/api/events$"), ("GET", r"^/api/status$"),
    ("GET", r"^/api/geocode$"), ("GET", r"^/api/reverse$"), ("POST", r"^/api/client-error$"),
    ("GET", r"^/api/me$"), ("PUT", r"^/api/me$"), ("GET", r"^/api/me/digest$"),
)]
# Wrong user-page links, per client: each distinct wrong link counts once, however often a stale tab
# retries it (an open page with a revoked link asks every 30 s, several requests at a time, and a
# whole household shares one address). Kept apart from the password lockout.
_bad_links: dict[str, dict[str, float]] = defaultdict(dict)
BAD_LINKS_MAX, BAD_LINKS_WINDOW = 10, 300
_viewer_seen: dict[int, float] = {}      # last time a viewer's visit was written / logged
_viewer_logged: dict[int, float] = {}


def _links_throttled(ip: str, now: float) -> bool:
    seen = {t: ts for t, ts in _bad_links[ip].items() if now - ts < BAD_LINKS_WINDOW}
    _bad_links[ip] = seen
    return len(seen) >= BAD_LINKS_MAX


DEAD_LINK_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="strict-origin"><title>Family Locator</title>
<style>body{font:17px/1.5 system-ui,sans-serif;max-width:32em;margin:15vh auto;padding:0 20px;color:#1e2b3a;background:#f6f4ef}
@media (prefers-color-scheme:dark){body{color:#e6e2da;background:#161b22}}</style>
<h1>This link doesn't work any more</h1>
<p>It may have been replaced with a new one. Use the link in your newest Family Locator email, or ask
the person who sent it for a new one.</p>"""


def viewer_of(req: Request) -> dict | None:
    """The user page this request came through, or None for the main page."""
    return req.scope.get("state", {}).get("viewer")


def viewer_devices(v: dict) -> list[str]:
    """Device ids a user page may see: the chosen people's devices, not hidden, no photo layers."""
    if not v["people"]:
        return []
    return [r["id"] for r in db.q(f"SELECT id FROM devices WHERE hidden=0 AND source!='photos' AND owner IN "
                                  f"({','.join('?' * len(v['people']))})", v["people"])]


def new_token() -> str:
    return secrets.token_urlsafe(24)


def render_page(viewer: bool = False) -> HTMLResponse:
    # Version the app's own JS/CSS URLs by file mtime so a browser can never pair a new page with a
    # stale cached script.
    html = (STATIC / "index.html").read_text()
    for f in ("app.js", "app.css", "rig.css", "rig-apps.js"):
        html = html.replace(f"static/{f}\"", f"static/{f}?v={int((STATIC / f).stat().st_mtime)}\"")
    if not viewer:
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
    # /v/<token>/ is two levels down; send only the origin as Referer so the token never leaves
    html = html.replace('"static/', '"../../static/').replace(
        "<head>", '<head>\n<meta name="referrer" content="strict-origin">', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Referrer-Policy": "strict-origin",
                                       "X-Robots-Tag": "noindex"})


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


def create_app(cfg, icloud, airtag, photos, alerter, tesla=None) -> FastAPI:
    app = FastAPI(title="Family Locator", version=__version__, docs_url=None, redoc_url=None)
    db.ensure_person_pages(new_token)

    @app.middleware("http")
    async def auth_mw(request: Request, call_next):
        # The radio-gateway proxy only forwards GET and POST; the UI tunnels PUT/PATCH/DELETE as POST.
        ov = request.headers.get("x-http-method-override", "").upper()
        if request.method == "POST" and ov in ("PUT", "PATCH", "DELETE"):
            request.scope["method"] = ov
        p = request.url.path
        m = VIEWER_PATH.match(p)
        if m:
            return await _viewer_request(request, call_next, m.group(1), m.group(2) or "")
        if p in OPEN_PATHS or p.startswith("/static/"):
            return await call_next(request)
        if not _authed(request):
            return JSONResponse({"error": "auth required"}, status_code=401)
        return await call_next(request)

    async def _viewer_request(request: Request, call_next, token: str, rest: str):
        ip, now = _client_ip(request), time.time()
        v = db.viewer_by_token(token)
        if not v:
            if token not in _bad_links[ip] and _links_throttled(ip, now):
                return JSONResponse({"error": "too many wrong links, wait 5 minutes"}, status_code=429)
            if token not in _bad_links[ip]:
                log.warning("[WebAccess] unknown user-page link from %s", ip)
            _bad_links[ip][token] = now
            if rest in ("", "/"):
                return HTMLResponse(DEAD_LINK_PAGE, status_code=404, headers={"Cache-Control": "no-store"})
            return JSONResponse({"error": "dead link"}, status_code=404)
        if _links_throttled(ip, now):
            return JSONResponse({"error": "too many wrong links, wait 5 minutes"}, status_code=429)
        if now - _viewer_seen.get(v["id"], 0) > 60:
            _viewer_seen[v["id"]] = now
            db.x("UPDATE viewers SET last_seen=? WHERE id=?", (now, v["id"]))
        if now - _viewer_logged.get(v["id"], 0) > 3600:
            _viewer_logged[v["id"]] = now
            log.info("[WebAccess] user page '%s' opened from %s", v["name"], ip)
        if rest == "":
            return RedirectResponse(f"{token}/", status_code=307)   # relative: works behind /family/ too
        if rest == "/":
            return render_page(viewer=True)
        method = request.scope["method"]
        if not any(method == mm and rx.match(rest) for mm, rx in VIEWER_ROUTES):
            return JSONResponse({"error": "not available on this page"}, status_code=403)
        request.scope["path"] = rest
        request.scope["raw_path"] = rest.encode()
        request.scope.setdefault("state", {})["viewer"] = v
        return await call_next(request)

    def _need_main(request: Request) -> None:
        if viewer_of(request):
            raise HTTPException(403, "not available on this page")

    # ------------------------------------------------------------- auth
    @app.get("/api/auth/state")
    def auth_state(request: Request):
        v = viewer_of(request)
        if v:
            return {"password_set": True, "authed": True, "version": __version__,
                    "viewer": {k: v[k] for k in ("name", "people", "email", "digest", "digest_hour")}}
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
    def devices(request: Request):
        rows = db.q("SELECT d.*, (SELECT COUNT(*) FROM points p WHERE p.device_id=d.id) AS n_points "
                    "FROM devices d ORDER BY d.source, d.owner, d.name")
        v = viewer_of(request)
        if v:
            allowed = set(viewer_devices(v))
            rows = [r for r in rows if r["id"] in allowed]
        for r in rows:
            r["meta"] = {} if v else json.loads(r["meta"] or "{}")
            last = db.q1("SELECT ts,lat,lon,acc,kind FROM points WHERE device_id=? ORDER BY ts DESC LIMIT 1",
                         (r["id"],))
            r["last"] = last
        return rows

    @app.patch("/api/devices/{dev_id:path}")
    def patch_device(dev_id: str, request: Request, body: dict = Body(...)):
        _need_main(request)
        for k in ("name", "color", "hidden"):
            if k in body:
                db.x(f"UPDATE devices SET {k}=? WHERE id=?", (body[k], dev_id))
        return {"ok": True}

    @app.get("/api/points")
    def get_points(request: Request, start: float, end: float, devices: str = "", max_acc: float = 0,
                   limit: int = 200000):
        sql = "SELECT device_id,ts,lat,lon,acc,alt,battery,kind,meta FROM points WHERE ts BETWEEN ? AND ?"
        args: list = [start, end]
        ids = [d for d in devices.split(",") if d]
        v = viewer_of(request)
        if v:
            allowed = viewer_devices(v)
            ids = [d for d in ids if d in allowed] if ids else allowed
            if not ids:
                return {"start": start, "end": end, "truncated": False, "points": {}}
        if ids:
            sql += f" AND device_id IN ({','.join('?' * len(ids))})"
            args += ids
        if max_acc:
            sql += " AND (acc IS NULL OR acc<=?)"
            args.append(max_acc)
        total = db.q1(sql.replace("device_id,ts,lat,lon,acc,alt,battery,kind,meta", "COUNT(*) AS n", 1), args)["n"]
        thinned = total > limit
        if thinned:
            # Too many fixes: thin each device's track evenly across the whole range instead of cutting
            # it off at a row limit (which used to drop the NEWEST fixes). Each device keeps its first
            # and last fix, and photos are never dropped.
            step = -(-total // limit)
            sql = ("SELECT device_id,ts,lat,lon,acc,alt,battery,kind,meta FROM ("
                   "SELECT *, ROW_NUMBER() OVER (PARTITION BY device_id ORDER BY ts) AS rn, "
                   "COUNT(*) OVER (PARTITION BY device_id) AS cnt FROM (" + sql + ")) "
                   "WHERE (rn - 1) % ? = 0 OR rn = cnt OR kind = 'photo'")
            args = args + [step]
        rows = db.q(sql + " ORDER BY ts", args)
        out: dict[str, list] = {}
        for r in rows:  # compact columnar-ish form: [ts, lat, lon, acc, battery, kind, meta]
            out.setdefault(r["device_id"], []).append(
                [r["ts"], round(r["lat"], 6), round(r["lon"], 6), r["acc"], r["battery"], r["kind"],
                 json.loads(r["meta"]) if r["meta"] else None])
        return {"start": start, "end": end, "truncated": thinned, "total": total, "points": out}

    @app.get("/api/range")
    def data_range(request: Request):
        v = viewer_of(request)
        if v:
            ids = viewer_devices(v) or ["-"]
            return db.q1(f"SELECT MIN(ts) AS first, MAX(ts) AS last, COUNT(*) AS n FROM points WHERE device_id IN "
                         f"({','.join('?' * len(ids))})", ids)
        return db.q1("SELECT MIN(ts) AS first, MAX(ts) AS last, COUNT(*) AS n FROM points")

    @app.get("/api/analysis")
    def get_analysis(request: Request, device: str, start: float, end: float):
        v = viewer_of(request)
        if v and device not in viewer_devices(v):
            raise HTTPException(404, "no such device")
        return analysis.summary(device, start, end, cfg.stay_radius_m, cfg.stay_min_minutes)

    # ------------------------------------------------------------- geofences
    # Each user page has its own places (viewer_id); the main page's are viewer_id NULL. Neither side
    # can see or change the other's.
    @app.get("/api/geofences")
    def list_fences(request: Request):
        v = viewer_of(request)
        rows = db.q("SELECT * FROM geofences WHERE viewer_id IS ? ORDER BY name", (v["id"] if v else None,))
        allowed = set(viewer_devices(v)) if v else None
        for r in rows:
            r["devices"] = json.loads(r["devices"]) if r["devices"] else []
            r["people"] = json.loads(r["people"]) if r.get("people") else []
            r["inside"] = [s["device_id"] for s in
                           db.q("SELECT device_id FROM fence_state WHERE geofence_id=? AND inside=1", (r["id"],))
                           if allowed is None or s["device_id"] in allowed]
        return rows

    def _fence_args(b: dict, v: dict | None):
        people, devs = b.get("people") or [], b.get("devices") or []
        if v:   # a user page can only aim its places at its own people
            people = [p for p in people if p in v["people"]]
            allowed = viewer_devices(v)
            devs = [d for d in devs if d in allowed]
        return (str(b["name"]), float(b["lat"]), float(b["lon"]), max(20.0, float(b.get("radius", 150))),
                b.get("color") or "#9a6b43", int(bool(b.get("on_enter", True))),
                int(bool(b.get("on_exit", True))), json.dumps(devs), json.dumps(people))

    def _own_fence(gid: int, v: dict | None) -> None:
        if not db.q1("SELECT id FROM geofences WHERE id=? AND viewer_id IS ?", (gid, v["id"] if v else None)):
            raise HTTPException(404, "no such place")

    @app.post("/api/geofences")
    def add_fence(request: Request, body: dict = Body(...)):
        v = viewer_of(request)
        cur = db.x("INSERT INTO geofences(name,lat,lon,radius,color,on_enter,on_exit,devices,people,viewer_id) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)", (*_fence_args(body, v), v["id"] if v else None))
        return {"id": cur.lastrowid}

    @app.put("/api/geofences/{gid}")
    def put_fence(gid: int, request: Request, body: dict = Body(...)):
        v = viewer_of(request)
        _own_fence(gid, v)
        db.x("UPDATE geofences SET name=?,lat=?,lon=?,radius=?,color=?,on_enter=?,on_exit=?,devices=?,people=? WHERE id=?",
             (*_fence_args(body, v), gid))
        db.x("DELETE FROM fence_state WHERE geofence_id=?", (gid,))  # re-learn silently after a move
        return {"ok": True}

    @app.delete("/api/geofences/{gid}")
    def del_fence(gid: int, request: Request):
        _own_fence(gid, viewer_of(request))
        db.x("DELETE FROM fence_state WHERE geofence_id=?", (gid,))
        db.x("DELETE FROM geofences WHERE id=?", (gid,))
        return {"ok": True}

    # ------------------------------------------------------------- events / status
    @app.get("/api/events")
    def events(request: Request, limit: int = 200, kind: str = ""):
        v = viewer_of(request)
        vid = v["id"] if v else None
        if kind:
            return db.q("SELECT * FROM events WHERE viewer_id IS ? AND kind=? ORDER BY ts DESC LIMIT ?", (vid, kind, limit))
        return db.q("SELECT * FROM events WHERE viewer_id IS ? ORDER BY ts DESC LIMIT ?", (vid, limit))

    @app.get("/api/status")
    def status(request: Request):
        if viewer_of(request):
            return {"version": __version__, "viewer": True}
        return {"version": __version__, "icloud": icloud.status(), "airtag": airtag.status(),
                "tesla": tesla.status() if tesla else None,
                "family_requests": icloud.tracker.family_status(time.time()),
                "photos": photos.status(), "backup": db.get_setting("last_backup"),
                "links": {"public": public_url(cfg), "lan": lan_url(cfg)}, "apps": cfg.apps,
                "tile_keys": {"thunderforest": cfg.thunderforest_key or None},
                "storage": {"db": str(db.DB_PATH), "points": db.q1("SELECT COUNT(*) AS n FROM points")["n"]}, "email": {"configured": alerter.email_configured(),
                                                     "recipient": cfg.email_recipient, **ALERT_COUNTERS},
                "config": {k: getattr(cfg, k) for k in ("poll_seconds", "locate_minutes", "airtag_poll_minutes",
                                                        "low_battery_pct", "stale_hours", "stay_radius_m",
                                                        "stay_min_minutes", "with_family")}}

    @app.get("/metrics")
    def metrics():
        lines = []
        for src, st in (("icloud", icloud.status()), ("airtag", airtag.status()),
                        *((("tesla", tesla.status()),) if tesla and tesla.state != "off" else ())):
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
    def people(request: Request):
        v = viewer_of(request)
        colors = db.get_setting("person_colors", {})
        names = db.get_setting("person_names", {})
        owners = [r["owner"] for r in db.q("SELECT owner, MIN(first_seen) f FROM devices WHERE owner IS NOT NULL "
                                           "AND source!='photos' GROUP BY owner ORDER BY f")]
        out = []
        for i, o in enumerate(owners):
            if v and o not in v["people"]:
                continue
            out.append({"name": o, "display": names.get(o) or o,
                        "color": colors.get(o) or PERSON_PALETTE[i % len(PERSON_PALETTE)],
                        "tracking": None if v else icloud.tracker.status(o, time.time())})
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

    # ------------------------------------------------------------- user pages
    def _viewer_out(v: dict) -> dict:
        path = f"v/{v['token']}/"
        pub = public_url(cfg)
        return {**{k: v[k] for k in ("id", "name", "email", "people", "digest", "digest_hour", "created", "last_seen",
                                    "auto_owner")},
                "link": (pub + path) if pub else None, "lan_link": lan_url(cfg) + path,
                "places": db.q1("SELECT COUNT(*) n FROM geofences WHERE viewer_id=?", (v["id"],))["n"]}

    def _viewer_fields(b: dict) -> dict:
        name = str(b.get("name") or "").strip()[:60]
        if not name:
            raise HTTPException(400, "give the page a name")
        email = str(b.get("email") or "").strip()[:200]
        if email and ("@" not in email or " " in email):
            raise HTTPException(400, "that email address doesn't look right")
        return {"name": name, "email": email or None,
                "people": json.dumps([str(p) for p in (b.get("people") or [])]),
                "digest": int(bool(b.get("digest"))),
                "digest_hour": min(23, max(0, int(b.get("digest_hour", 8))))}

    @app.get("/api/viewers")
    def list_viewers(request: Request):
        _need_main(request)
        db.ensure_person_pages(new_token)
        return [_viewer_out(db.viewer(r["id"])) for r in db.q("SELECT id FROM viewers ORDER BY name")]

    @app.post("/api/viewers")
    def add_viewer(request: Request, body: dict = Body(...)):
        _need_main(request)
        f = _viewer_fields(body)
        cur = db.x("INSERT INTO viewers(name,email,people,digest,digest_hour,token,created) VALUES(?,?,?,?,?,?,?)",
                   (*f.values(), new_token(), time.time()))
        return _viewer_out(db.viewer(cur.lastrowid))

    @app.put("/api/viewers/{vid}")
    def put_viewer(vid: int, request: Request, body: dict = Body(...)):
        _need_main(request)
        if not db.viewer(vid):
            raise HTTPException(404, "no such user page")
        f = _viewer_fields(body)
        db.x("UPDATE viewers SET name=?,email=?,people=?,digest=?,digest_hour=? WHERE id=?", (*f.values(), vid))
        return _viewer_out(db.viewer(vid))

    @app.delete("/api/viewers/{vid}")
    def del_viewer(vid: int, request: Request):
        _need_main(request)
        v = db.viewer(vid)
        if v and v.get("auto_owner"):   # don't make it again
            db.set_setting("person_pages_off", sorted(set(db.get_setting("person_pages_off", [])) | {v["auto_owner"]}))
        for g in db.q("SELECT id FROM geofences WHERE viewer_id=?", (vid,)):
            db.x("DELETE FROM fence_state WHERE geofence_id=?", (g["id"],))
        db.x("DELETE FROM geofences WHERE viewer_id=?", (vid,))
        db.x("DELETE FROM events WHERE viewer_id=?", (vid,))
        db.x("DELETE FROM viewers WHERE id=?", (vid,))
        return {"ok": True}

    @app.post("/api/viewers/{vid}/new-link")
    def new_viewer_link(vid: int, request: Request):
        """Replace the link: the old one stops working straight away."""
        _need_main(request)
        if not db.viewer(vid):
            raise HTTPException(404, "no such user page")
        db.x("UPDATE viewers SET token=? WHERE id=?", (new_token(), vid))
        return _viewer_out(db.viewer(vid))

    @app.post("/api/viewers/{vid}/send-link")
    def send_viewer_link(vid: int, request: Request):
        """Email the user page's link to its address now."""
        _need_main(request)
        v = db.viewer(vid)
        if not v:
            raise HTTPException(404, "no such user page")
        from .alerts import viewer_link
        link = viewer_link(cfg, v)
        if not link:
            raise HTTPException(503, "the internet link (Cloudflare tunnel) is down, so there's no link to send")
        names = ", ".join(db.person_display(p) or p for p in v["people"]) or "nobody yet"
        err = alerter.send_email("Your Family Locator link",
                                 f"Hello {v['name']},\n\nThis link shows where {names} are, and where they've "
                                 f"been. Keep it to yourself: anyone with the link can open it.\n\nOpen: {link}\n\n"
                                 "The link can change; the newest one is always in the latest email.", viewer=v)
        if err:
            raise HTTPException(502, err)
        return {"ok": True, "to": v["email"]}

    @app.get("/api/viewers/{vid}/digest")
    def viewer_digest_preview(vid: int, request: Request):
        _need_main(request)
        v = db.viewer(vid)
        if not v:
            raise HTTPException(404, "no such user page")
        from .alerts import build_digest
        subj, body = build_digest(cfg, viewer=v)
        return {"subject": subj, "body": body}

    @app.get("/api/me/digest")
    def me_digest(request: Request):
        v = viewer_of(request)
        if not v:
            raise HTTPException(404, "only on a user page")
        from .alerts import build_digest
        subj, body = build_digest(cfg, viewer=v)
        return {"subject": subj, "body": body}

    @app.get("/api/me")
    def me(request: Request):
        v = viewer_of(request)
        if not v:
            raise HTTPException(404, "only on a user page")
        return {k: v[k] for k in ("name", "people", "email", "digest", "digest_hour")}

    @app.put("/api/me")
    def put_me(request: Request, body: dict = Body(...)):
        """A user may switch their own daily email on or off; the address is set on the main page."""
        v = viewer_of(request)
        if not v:
            raise HTTPException(404, "only on a user page")
        if "digest" in body:
            db.x("UPDATE viewers SET digest=? WHERE id=?", (int(bool(body["digest"])), v["id"]))
        return me_after(v["id"])

    def me_after(vid: int) -> dict:
        v = db.viewer(vid)
        return {k: v[k] for k in ("name", "people", "email", "digest", "digest_hour")}

    @app.get("/")
    def index():
        return render_page()

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
