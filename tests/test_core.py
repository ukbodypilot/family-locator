"""Run: FAMLOC_DATA=$(mktemp -d) .venv/bin/python -m pytest -q tests  (or plain python)."""
import os, sys, tempfile
os.environ.setdefault("FAMLOC_DATA", tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from famloc import config, db, analysis
from famloc.alerts import Alerter

db.init()
cfg = config.Config(email_enabled=False)
al = Alerter(cfg)

def _dev():
    db.upsert_device("icloud:t", "icloud", "Test phone", owner="T")
    return db.q1("SELECT * FROM devices WHERE id='icloud:t'")

def test_geofence_enter_exit_hysteresis():
    dev = _dev()
    gid = db.x("INSERT INTO geofences(name,lat,lon,radius,on_enter,on_exit) VALUES('Home',37.0,-122.0,150,1,1)").lastrowid
    seq = [(0, 37.0, -122.0005),   # inside -> initial state, silent
           (60, 37.003, -122.0),   # ~333 m out -> exit
           (120, 37.0015, -122.0), # ~167 m: inside the margin band -> nothing
           (180, 37.0, -122.0),    # back inside -> enter
           (100, 37.01, -122.0)]   # OLDER than state -> ignored
    for ts, la, lo in seq:
        al.check_fix(dev, 1000 + ts, la, lo, 10)
    kinds = [e["kind"] for e in db.q("SELECT kind FROM events WHERE geofence_id=? ORDER BY ts", (gid,))]
    assert kinds == ["exit", "enter"], kinds

def test_bad_accuracy_ignored():
    dev = _dev()
    gid = db.x("INSERT INTO geofences(name,lat,lon,radius) VALUES('Far',10.0,10.0,100)").lastrowid
    al.check_fix(dev, 5000, 10.0, 10.0, 5000)
    assert db.q1("SELECT * FROM fence_state WHERE geofence_id=?", (gid,)) is None

def test_battery_alert_once():
    dev = _dev()
    for b in (0.5, 0.1, 0.09, 0.08):
        al.check_battery(dev, b)
    assert len(db.q("SELECT * FROM events WHERE kind='battery'")) == 1
    al.check_battery(dev, 0.9); al.check_battery(dev, 0.1)
    assert len(db.q("SELECT * FROM events WHERE kind='battery'")) == 2

def test_stays_and_trips():
    pts = [{"ts": i * 300, "lat": 37.0, "lon": -122.0, "acc": 10} for i in range(10)]
    pts += [{"ts": 3000 + i * 300, "lat": 37.0 + i * 0.005, "lon": -122.0, "acc": 10} for i in range(1, 6)]
    pts += [{"ts": 4800 + i * 300, "lat": 37.03, "lon": -122.0, "acc": 10} for i in range(10)]
    st = analysis.stays(pts, 150, 10)
    assert len(st) == 2, st
    tr = analysis.trips(pts, st)
    assert len(tr) == 1 and 2.5 < tr[0]["km"] < 4, tr

def test_dedup_points():
    _dev()
    assert db.add_point("icloud:t", 42.0, 1, 2) is True
    assert db.add_point("icloud:t", 42.0, 1, 2) is False



def test_person_dedupe_phone_and_watch():
    db.upsert_device("icloud:p1", "icloud", "Kid phone", owner="Kid", device_class="iPhone")
    db.upsert_device("icloud:w1", "icloud", "Kid watch", owner="Kid", device_class="Watch")
    gid = db.x("INSERT INTO geofences(name,lat,lon,radius) VALUES('School',40.0,-100.0,150)").lastrowid
    sent = []
    orig = al.notify
    al.notify = lambda kind, subj, msg, **kw: (sent.append((kind, kw.get("email", True))), orig(kind, subj, msg, **kw))
    try:
        for dev_id in ("icloud:p1", "icloud:w1"):
            dev = db.q1("SELECT * FROM devices WHERE id=?", (dev_id,))
            al.check_fix(dev, 100, 40.0, -100.0, 10)        # learn: inside
            al.check_fix(dev, 200, 40.01, -100.0, 10)       # ~1.1 km out: exit
    finally:
        al.notify = orig
    assert [k for k, _ in sent] == ["exit", "exit"], sent
    assert [e for _, e in sent] == [True, False], "second device of same person must not email"


def test_people_filter():
    db.upsert_device("icloud:o1", "icloud", "Other", owner="Other", device_class="iPhone")
    gid = db.x("INSERT INTO geofences(name,lat,lon,radius,people) VALUES('Only kid',41.0,-100.0,150,'[\"Kid\"]')").lastrowid
    dev = db.q1("SELECT * FROM devices WHERE id='icloud:o1'")
    al.check_fix(dev, 100, 41.0, -100.0, 10)
    assert db.q1("SELECT * FROM fence_state WHERE geofence_id=?", (gid,)) is None


def test_quiet_hours():
    import time as _t
    c = config.Config(email_enabled=False, quiet_from_hour=22, quiet_to_hour=7)
    a = Alerter(c)
    base = _t.mktime((2026, 1, 1, 0, 0, 0, 0, 0, -1))
    assert a.in_quiet_hours(base + 23 * 3600) and a.in_quiet_hours(base + 3 * 3600)
    assert not a.in_quiet_hours(base + 12 * 3600)
    assert not Alerter(config.Config()).in_quiet_hours(base)


def test_digest_builds():
    from famloc.alerts import build_digest
    subj, body = build_digest(cfg)
    assert "Daily summary" in subj and "Kid" in body


def test_person_display_name():
    db.set_setting("person_names", {"Kid": "Kiddo"})
    from famloc.alerts import build_digest
    assert db.person_display("Kid") == "Kiddo" and db.person_display("Other") == "Other"
    assert "Kiddo:" in build_digest(cfg)[1]


def test_tracker_intervals_and_overrides():
    from famloc.tracking import Tracker
    c = config.Config(locate_minutes=15, moving_locate_minutes=2)
    t = Tracker(c)
    t.set_person("Kid", locate_minutes=5)
    t.set_person("Gran", locate_minutes=0)
    assert t.interval_minutes("Kid", 0) == 5 and t.interval_minutes("Other", 0) == 15
    assert t.due(["Gran"], 1000) == []            # never
    t.set_person("Kid", locate_minutes=1)          # 1 minute is allowed
    assert t.person_settings("Kid")["locate_minutes"] == 1
    t.set_person("Kid", locate_minutes=0.5)        # below a minute rounds down to never
    assert t.person_settings("Kid")["locate_minutes"] == 0
    t.set_person("Kid", locate_minutes=None)       # back to the default
    assert "locate_minutes" not in t.person_settings("Kid")
    db.set_setting("person_tracking", {})


def test_tracker_due_staggers_then_respects_interval():
    from famloc.tracking import Tracker
    t = Tracker(config.Config(locate_minutes=15))
    assert t.due(["A", "B", "C"], 10_000) == ["A"]          # B, C staggered 20 s / 40 s later
    assert t.due(["A", "B", "C"], 10_025) == ["A", "B"]     # A not marked sent yet
    for o in ("A", "B", "C"):
        t.mark_sent(o, 10_050)
    assert t.due(["A", "B", "C"], 10_050 + 14 * 60) == []
    assert t.due(["A", "B", "C"], 10_050 + 15 * 60) == ["A", "B", "C"]


def test_tracker_movement_ignores_wobble():
    from famloc.tracking import Tracker
    t = Tracker(config.Config(movement_threshold_m=250, follow_rest_minutes=10))
    still = [(0, 40.0, -100.0, 30), (120, 40.0009, -100.0, 65)]     # ~100 m apart, wobble
    assert not t.moving(still)
    poor = [(0, 40.0, -100.0, 400), (120, 40.005, -100.0, 300)]     # 556 m but accuracy 700 m
    assert not t.moving(poor)
    drive = [(0, 40.0, -100.0, 10), (120, 40.004, -100.0, 10)]      # 445 m in 2 min
    assert t.moving(drive)
    stale = [(0, 40.0, -100.0, 10), (3000, 40.004, -100.0, 10)]     # previous fix too old to compare
    assert not t.moving(stale)


def test_tracker_follow_lifecycle_and_cap():
    from famloc.tracking import Tracker
    c = config.Config(locate_minutes=15, moving_locate_minutes=2, follow_rest_minutes=10, follow_max_hours=1)
    t = Tracker(c)
    drive = [(1000, 40.0, -100.0, 10), (1120, 40.004, -100.0, 10)]
    assert t.on_fixes("Kid", drive, 1120)
    assert t.following("Kid", 1121) == "moving" and t.interval_minutes("Kid", 1121) == 2
    assert t.due(["Kid"], 1121) == ["Kid"]                         # first fast locate is due at once
    t.mark_sent("Kid", 1121)
    parked = [(1120, 40.004, -100.0, 10)]
    for k in range(1, 6):                                          # 2-minute fixes, parked
        parked.append((1120 + k * 120, 40.0041, -100.0, 10))
        still_following = t.on_fixes("Kid", parked, parked[-1][0])
        assert still_following == (k < 4), (k, still_following)    # needs ~8 of the 10 rest minutes
    assert t.following("Kid", parked[-1][0] + 1) is None
    # runaway cap: keeps "moving" for over an hour -> stops and cools down
    t2 = Tracker(c)
    now = 0
    for i in range(40):
        now = i * 120
        t2.on_fixes("Car", [(now - 120, 40.0 + i * 0.004, -100.0, 10), (now, 40.004 + i * 0.004, -100.0, 10)], now)
    assert t2.following("Car", now + 1) is None and t2.cap_until["Car"] > now
    db.set_setting("person_tracking", {"Off": {"follow": False}})
    assert not t.on_fixes("Off", drive, 1120)
    db.set_setting("person_tracking", {})


def test_tracker_manual_follow():
    from famloc.tracking import Tracker
    t = Tracker(config.Config(locate_minutes=15, moving_locate_minutes=2))
    t.start_manual("Kid", 30, 5000)
    assert t.following("Kid", 5001) == "manual" and t.due(["Kid"], 5001) == ["Kid"]
    t.start_manual("Kid", 0, 5100)
    assert t.following("Kid", 5101) is None


def test_poller_follows_a_moving_person_end_to_end():
    """Fake Find My: a phone drives ~4 km then parks. The poller must locate on the normal
    interval, switch to 2-minute locates while moving, and drop back once parked."""
    from famloc import icloud_source
    from famloc.icloud_source import ICloudSource

    class FakeDev:
        def __init__(self, data): self.data = data
    class FakeMgr:
        def __init__(self):
            self.t = 0.0; self.lat = 41.5; self.posts = []; self.reads = 0
            self.user_info = {"firstName": "Pat", "lastName": "", "membersInfo": {}}
            self._server_ctx = {}; self._fmip_refresh_url = "x"; self.params = {}
            self._refresh_lock = threading.Lock()
            mgr = self
            class Sess:
                def post(self, url, params=None, json=None, timeout=None):
                    mgr.posts.append(mgr.t)
                    class R:
                        def json(self_inner): return {"serverContext": {}}
                    return R()
            self.session = Sess()
        def _dev(self):
            return FakeDev({"id": "raw1", "deviceDiscoveryId": "pat-phone", "name": "Pat's iPhone",
                            "deviceClass": "iPhone", "deviceDisplayName": "iPhone", "batteryLevel": 0.8,
                            "locationCapable": True,
                            "location": {"latitude": self.lat, "longitude": -100.0, "horizontalAccuracy": 10,
                                         "timeStamp": int(self.t * 1000)}})
        def __iter__(self): return iter([self._dev()])
        def refresh(self, locate=False): self.reads += 1

    import threading
    cfg2 = config.Config(email_enabled=False, poll_seconds=300, locate_minutes=15, moving_locate_minutes=2,
                         follow_rest_minutes=10, movement_threshold_m=250)
    src = ICloudSource(cfg2, Alerter(cfg2))
    mgr = FakeMgr()
    class Api: devices = mgr
    src.api = Api(); src.state = "ok"
    real_time = icloud_source.time.time
    clock = {"t": 2_000_000.0}
    icloud_source.time.time = lambda: clock["t"]
    try:
        def run(seconds, speed_deg_per_tick=0.0):
            for _ in range(int(seconds / 15)):
                clock["t"] += 15; mgr.t = clock["t"]; mgr.lat += speed_deg_per_tick
                src.tick(clock["t"])
        run(30 * 60)                                  # parked 30 min: locates every 15 min
        parked_posts = len(mgr.posts)
        assert 2 <= parked_posts <= 3, mgr.posts
        run(20 * 60, speed_deg_per_tick=0.0005)       # driving ~6.7 km in 20 min
        assert src.tracker.following("Pat", clock["t"]) == "moving"
        drive_posts = len(mgr.posts) - parked_posts
        assert drive_posts >= 7, drive_posts          # roughly every 2 min
        run(30 * 60)                                  # parked again
        assert src.tracker.following("Pat", clock["t"]) is None
        gaps = [b - a for a, b in zip(mgr.posts, mgr.posts[1:])]
        assert gaps[-1] >= 15 * 60 - 15, gaps[-3:]    # back on the normal interval
    finally:
        icloud_source.time.time = real_time


def test_tracker_devices_never_compared_with_each_other():
    from famloc.tracking import Tracker
    t = Tracker(config.Config(movement_threshold_m=250, follow_rest_minutes=10))
    ipad_home = [(1000, 40.0, -100.0, 10), (1300, 40.0, -100.0, 10)]
    phone_away = [(1100, 40.02, -100.0, 10), (1250, 40.0201, -100.0, 10)]   # 2 km from the iPad, not moving
    assert not t.on_fixes("Me", [ipad_home, phone_away], 1300)
    # following stops only when nobody's device moved in the rest window
    phone_drive = [(2000, 40.0, -100.0, 10), (2120, 40.005, -100.0, 10)]
    assert t.on_fixes("Me", [ipad_home, phone_drive], 2120)
    phone_parked = phone_drive + [(2120 + k * 120, 40.0051, -100.0, 10) for k in range(1, 6)]
    ipad_rest = [(1700 + k * 120, 40.0, -100.0, 10) for k in range(0, 6)]     # covers 1700..2300
    assert t.on_fixes("Me", [ipad_rest, phone_parked[:3]], 2300)      # iPad at rest, but phone moved in window
    ipad_rest2 = [(1700 + k * 120, 40.0, -100.0, 10) for k in range(0, 9)]    # ..2660
    assert not t.on_fixes("Me", [ipad_rest2, phone_parked], 2720)     # phone's window now clear: stop


def test_config_floors():
    c = config.Config()
    got = config.apply_overrides(c, {"locate_minutes": 0.4, "moving_locate_minutes": 0.1})
    assert got["locate_minutes"] == 1 or got["locate_minutes"] == 0  # int(0.4) -> 0 = never
    assert got["moving_locate_minutes"] == 0.5
    got = config.apply_overrides(c, {"locate_minutes": 1, "moving_locate_minutes": 0.5})
    assert got == {"locate_minutes": 1, "moving_locate_minutes": 0.5}


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
