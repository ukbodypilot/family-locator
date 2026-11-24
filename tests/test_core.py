"""Run: FAMLOC_DATA=$(mktemp -d) .venv/bin/python -m pytest -q tests  (or plain python).
The user-page API tests use FastAPI's TestClient, which needs httpx: .venv/bin/pip install httpx"""
import json, os, sys, tempfile
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


def test_late_device_does_not_report_an_old_crossing():
    """A laptop asleep in the bag: its last fix is at School, its next one at Home. The phone already
    sent 'left School' and 'arrived Home'; the laptop's late 'left School' must not email."""
    for i, cls in (("p2", "iPhone"), ("m2", "Mac")):
        db.upsert_device(f"icloud:{i}", "icloud", f"Sam {cls}", owner="Sam", device_class=cls)
    sch = db.x("INSERT INTO geofences(name,lat,lon,radius) VALUES('School2',41.0,-100.0,150)").lastrowid
    hom = db.x("INSERT INTO geofences(name,lat,lon,radius) VALUES('Home2',41.05,-100.0,150)").lastrowid
    phone, mac = (db.q1("SELECT * FROM devices WHERE id=?", (i,)) for i in ("icloud:p2", "icloud:m2"))
    sent = []
    orig = al.notify
    al.notify = lambda kind, subj, msg, **kw: (sent.append((subj, kw.get("email", True))), orig(kind, subj, msg, **kw))
    try:
        for d in (phone, mac):
            al.check_fix(d, 1000, 41.0, -100.0, 10)           # both learn: at School, not Home
        al.check_fix(phone, 2000, 41.01, -100.0, 10)          # phone leaves School
        al.check_fix(phone, 3400, 41.05, -100.0, 10)          # phone arrives Home
        al.check_fix(mac, 3600, 41.05, -100.0, 10)            # laptop wakes up at Home, 27 min later
        al.check_fix(phone, 9000, 41.0, -100.0, 10)           # next day-ish: phone back at School
        al.check_fix(phone, 9500, 41.01, -100.0, 10)          # and leaves again: real news
    finally:
        al.notify = orig
    emailed = [s for s, e in sent if e]
    assert emailed == ["Sam left School2", "Sam arrived at Home2", "Sam left Home2",
                       "Sam arrived at School2", "Sam left School2"], sent   # left before arrived, same fix

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
    assert t.due(["A", "B", "C"], 10_000) == []             # nothing in the first 2 minutes after start
    assert t.due(["A", "B", "C"], 10_120) == ["A"]          # B, C staggered 30 s / 60 s later
    assert t.due(["A", "B", "C"], 10_150) == ["A", "B"]     # A not marked sent yet
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


def test_tracker_backs_off_unanswered_and_recovers():
    from famloc.tracking import Tracker
    t = Tracker(config.Config(locate_minutes=15, moving_locate_minutes=2))
    now = 0.0
    for i in range(3):                       # three requests, no fix follows any of them
        t.mark_sent("Kid", now); now += 200; t.check_timeouts(now)
    assert t.streak["Kid"] == 3 and t.backoff_factor("Kid") == 2 and t.interval_minutes("Kid", now) == 30
    t.mark_sent("Kid", now); now += 200; t.check_timeouts(now)
    assert t.interval_minutes("Kid", now) == 60      # 4x, capped at an hour
    t.mark_sent("Kid", now); t.note_fix("Kid", now + 20)   # phone answers
    assert t.streak["Kid"] == 0 and t.interval_minutes("Kid", now) == 15
    t.start_manual("Kid", 30, now)                          # manual follow is never slowed
    t.streak["Kid"] = 9
    assert t.interval_minutes("Kid", now + 1) == 2


def test_own_device_at_rest_is_not_backed_off():
    """Apple acted on our own phone's locate (isLocating=true) but kept the stored fix because the
    phone hasn't moved: that is not a reason to slow down. Ignored requests still back off."""
    from famloc.tracking import Tracker
    t = Tracker(config.Config(locate_minutes=5, moving_locate_minutes=2))
    t.self_owner = "Me"
    now = 0.0
    for i in range(5):
        t.record_reply("Me", True, now); t.mark_sent("Me", now); now += 200; t.check_timeouts(now)
    assert t.streak.get("Me", 0) == 0 and t.interval_minutes("Me", now) == 5
    for i in range(3):                       # Apple didn't act: these count
        t.record_reply("Me", False, now); t.mark_sent("Me", now); now += 200; t.check_timeouts(now)
    assert t.streak["Me"] == 3 and t.interval_minutes("Me", now) == 10


def _gov_tracker(**kw):
    from famloc.tracking import Tracker
    db.set_setting("locate_governor", {})
    t = Tracker(config.Config(family_locates_per_hour=kw.get("budget", 20), family_pause_minutes=15, locate_minutes=15, moving_locate_minutes=2))
    t.self_owner = "Me"
    return t


def test_governor_budget_and_spacing():
    t = _gov_tracker(budget=5)
    now, sent = 0.0, []
    fam = ["A", "B", "C"]
    for _ in range(240):                                    # one hour of 15 s ticks, everyone always due
        pick = t.select(fam, now)
        for o in pick:
            t.record_request(o, now); t.record_reply(o, True, now); t.mark_sent(o, now); sent.append(now)
        now += 15
    assert len(sent) == 5, len(sent)                        # family budget per rolling hour
    assert all(b - a >= 20 for a, b in zip(sent, sent[1:]))


def test_governor_self_exempt_priority_and_pause():
    t = _gov_tracker()
    now = 1000.0
    t.start_manual("B", 30, now)
    assert t.select(["A", "B"], now) == ["B"]               # manual follow first
    t.record_request("B", now)
    assert t.select(["A", "Me"], now + 5) == []             # 20 s spacing applies to everyone
    for i in range(3):                                      # Apple ignores 3 family requests in a row
        now += 30; t.record_request("A", now); t.record_reply("A", False, now)
    assert t.family_paused(now + 1) and t.family_status(now + 1)["pause_level"] == 1
    assert t.select(["A", "B"], now + 60) == []             # family paused...
    assert t.select(["A", "Me"], now + 60) == ["Me"]        # ...own devices are not
    now += 15 * 60 + 1                                      # pause over: one probe allowed
    assert t.select(["A"], now) == ["A"]
    t.record_request("A", now); t.record_reply("A", False, now)   # probe ignored too -> 30 min
    assert t.family_status(now)["pause_level"] == 2 and t.gov["until"] - now == 30 * 60
    now += 30 * 60 + 1
    t.record_request("A", now); t.record_reply("A", True, now)    # Apple acts again -> cleared
    assert not t.family_paused(now) and t.family_status(now)["pause_level"] == 0


def test_governor_survives_restart():
    from famloc.tracking import Tracker
    t = _gov_tracker(budget=3)
    for i in range(3):
        t.record_request("A", 5000.0 + i * 30)
    t.record_reply("A", False, 5100); t.record_reply("A", False, 5100); t.record_reply("A", False, 5100)
    t2 = Tracker(t.cfg); t2.self_owner = "Me"               # "restart"
    assert t2.family_used(5200) == 3 and t2.family_paused(5200)
    assert t2.select(["A"], 5200) == []
    db.set_setting("locate_governor", {})


def test_late_fix_is_not_an_answer():
    t = _gov_tracker()
    t.mark_sent("Kid", 1000.0)
    t.note_fix("Kid", 1000.0 + 400, "iPhone")        # 400 s later: outside the 3-minute window
    assert t.answered.get("Kid", 0) == 0
    t.note_fix("Kid", 1000.0 + 60, "iPhone")         # 60 s: an answer
    assert t.answered["Kid"] == 1
    ev = [r["event"] for r in db.q("SELECT event FROM locate_events WHERE person='Kid' ORDER BY id")]
    assert ev[-2:] == ["fix", "answer"], ev


def test_session_renewed_before_apple_lifespan():
    import threading
    from famloc import icloud_source
    from famloc.icloud_source import ICloudSource
    class M:
        def __init__(self):
            self._server_ctx = None; self.inits = []; self.t = 0
            self.user_info = {"firstName": "Pat", "lastName": "", "membersInfo": {}}
            self._refresh_lock = threading.Lock()
        def __iter__(self): return iter([])
        def refresh(self, locate=False):
            if self._server_ctx is None:
                self.inits.append(self.t)
            self._server_ctx = {"sessionLifespan": 900000}
    c2 = config.Config(email_enabled=False, poll_seconds=60, session_renew_minutes=14)
    src = ICloudSource(c2, Alerter(c2)); m = M()
    class Api: devices = m
    src.api = Api(); src.state = "ok"
    real = icloud_source.time.time; clock = {"t": 3_000_000.0}
    icloud_source.time.time = lambda: clock["t"]
    try:
        for _ in range(int(40 * 60 / 15)):
            clock["t"] += 15; m.t = round((clock["t"] - 3_000_000) / 60, 1)
            src.tick(clock["t"])
    finally:
        icloud_source.time.time = real
    assert len(m.inits) == 3, m.inits                     # ~0, 14, 28 min
    assert all(14 <= b - a <= 15.5 for a, b in zip(m.inits, m.inits[1:])), m.inits


def test_follow_targets_the_moving_device_not_the_one_left_behind():
    from famloc.tracking import Tracker
    t = Tracker(config.Config(movement_threshold_m=250, follow_rest_minutes=10, moving_locate_minutes=2))
    phone_home = [(1000, 40.0, -100.0, 10), (1200, 40.0, -100.0, 10)]           # iPhone left at home
    watch_out = [(1000, 40.0, -100.0, 10), (1150, 40.006, -100.0, 10)]          # Watch leaves with them
    assert t.on_fixes("Pat", {"iphone": phone_home, "watch": watch_out}, 1200)
    assert t.locate_target("Pat", 1201) == "watch"
    # the watch keeps moving: still followed, still the target; the still phone never takes over
    watch_more = watch_out + [(1270, 40.012, -100.0, 10)]
    phone_more = phone_home + [(1300, 40.0, -100.0, 10)]
    assert t.on_fixes("Pat", {"iphone": phone_more, "watch": watch_more}, 1300) and t.locate_target("Pat", 1301) == "watch"
    # a phone at rest for the whole window doesn't end the follow while the watch moved recently
    assert t.following("Pat", 1301) == "moving"
    # the watch parks: rest window fills -> follow ends, target falls back to the main device
    parked = watch_more + [(1270 + k * 120, 40.0121, -100.0, 10) for k in range(1, 6)]
    assert not t.on_fixes("Pat", {"iphone": phone_more, "watch": parked}, parked[-1][0])
    assert t.locate_target("Pat", parked[-1][0] + 1) is None


def test_sessions_renew_faster_while_family_moves():
    import threading
    from famloc import icloud_source
    from famloc.icloud_source import ICloudSource
    class M:
        def __init__(self):
            self._server_ctx = None; self.inits = []; self.t = 0
            self.user_info = {"firstName": "Me", "lastName": "", "membersInfo": {}}
            self._refresh_lock = threading.Lock()
        def __iter__(self): return iter([])
        def refresh(self, locate=False):
            if self._server_ctx is None: self.inits.append(self.t)
            self._server_ctx = {"sessionLifespan": 900000}
    c2 = config.Config(email_enabled=False, poll_seconds=60, session_renew_minutes=5, session_renew_moving_minutes=3)
    src = ICloudSource(c2, Alerter(c2)); m = M()
    class Api: devices = m
    src.api = Api(); src.state = "ok"
    db.upsert_device("icloud:kid-phone", "icloud", "Kid phone", owner="Kid", device_class="iPhone")
    real = icloud_source.time.time; clock = {"t": 4_000_000.0}
    icloud_source.time.time = lambda: clock["t"]
    try:
        def run(minutes):
            for _ in range(int(minutes * 60 / 15)):
                clock["t"] += 15; m.t = round((clock["t"] - 4_000_000) / 60, 2); src.tick(clock["t"])
        run(20)                                               # nobody moving: every 5 min
        quiet = list(m.inits)
        src.tracker.follow_until["Kid"] = clock["t"] + 15 * 60   # Kid starts moving for 15 min
        src.tracker.follow_started["Kid"] = clock["t"]
        run(15)
        moving = [t for t in m.inits if t > quiet[-1]]
        run(20)                                               # at rest again
        after = [t for t in m.inits if t > 35]
    finally:
        icloud_source.time.time = real
    gap = lambda xs: [round(b - a, 1) for a, b in zip(xs, xs[1:])]
    assert all(4.9 <= g <= 5.5 for g in gap(quiet)), gap(quiet)
    assert all(2.9 <= g <= 3.5 for g in gap(moving)), gap(moving)
    assert all(4.9 <= g <= 5.5 for g in gap(after)), gap(after)
    ev = db.q("SELECT data FROM locate_events WHERE event='session_renew' ORDER BY id DESC LIMIT 20")
    assert any("moving: Kid" in r["data"] for r in ev)


def test_quiet_mode_slows_sessions_when_everyone_rests():
    import threading
    from famloc import icloud_source
    from famloc.icloud_source import ICloudSource
    class M:
        def __init__(self):
            self._server_ctx = None; self.inits = []; self.t = 0
            self.user_info = {"firstName": "Me", "lastName": "", "membersInfo": {}}
            self._refresh_lock = threading.Lock()
        def __iter__(self): return iter([])
        def refresh(self, locate=False):
            if self._server_ctx is None: self.inits.append(self.t)
            self._server_ctx = {"sessionLifespan": 900000}
    c2 = config.Config(email_enabled=False, poll_seconds=60, session_renew_minutes=5, session_renew_quiet_minutes=10, quiet_after_minutes=30)
    src = ICloudSource(c2, Alerter(c2)); m = M()
    class Api: devices = m
    src.api = Api(); src.state = "ok"
    db.upsert_device("icloud:quiet-kid", "icloud", "Kid phone", owner="QuietKid", device_class="iPhone")
    db.x("UPDATE devices SET hidden=1 WHERE id != 'icloud:quiet-kid'")   # only this family member counts
    real = icloud_source.time.time; clock = {"t": 5_000_000.0}
    icloud_source.time.time = lambda: clock["t"]
    try:
        def run(minutes, moving=False):
            for _ in range(int(minutes * 60 / 15)):
                clock["t"] += 15; m.t = round((clock["t"] - 5_000_000) / 60, 2)
                if int(clock["t"]) % 120 < 15:   # a fix every 2 min: still, or drifting 500 m per fix
                    k = (clock["t"] - 5_000_000) / 120
                    db.add_point("icloud:quiet-kid", clock["t"], 40.0 + (0.0045 * k if moving else 0), -100.0, acc=10)
                src.tick(clock["t"])
        run(20, moving=True)                     # travelling: not quiet
        busy = len(m.inits)
        run(60)                                  # resting: after 30 min quiet kicks in
    finally:
        icloud_source.time.time = real
        db.x("UPDATE devices SET hidden=0")
    gap = lambda xs: [round(b - a, 1) for a, b in zip(xs, xs[1:])]
    late = [t for t in m.inits if t > 20 + 32]
    assert src.session_mode == "quiet", src.session_mode
    assert all(g >= 9.9 for g in gap(late)), gap(m.inits)
    early = [t for t in m.inits if t <= 20]
    assert all(g <= 5.5 for g in gap(early)), gap(early)


def test_resume_network_error_retries_not_need_login():
    """2026-11-22: a boot-time DNS failure was treated as an expired session and tracking stopped."""
    import requests
    from pyicloud.exceptions import PyiCloudConnectionException
    from famloc import icloud_source

    sent = []
    class _Alerts:
        def notify(self, *a, **k): sent.append(a)

    db.set_setting("icloud_apple_id", "someone@example.com")
    db.set_setting("icloud_need_login_mailed", False)
    src = icloud_source.ICloudSource(cfg, _Alerts())

    def boom(aid, pw):
        try:
            raise requests.ConnectionError("Temporary failure in name resolution")
        except requests.ConnectionError as inner:
            raise PyiCloudConnectionException("Request failed to iCloud") from inner
    src._make = boom
    src.resume()
    assert src.state == "error" and src.api is None, src.state
    assert src._resume_retry_at > 0 and not sent

    api = type("A", (), {"requires_2fa": False, "requires_2sa": False})()
    src._make = lambda aid, pw: api
    src.resume()
    assert src.state == "ok" and src.api is api

    def bad(aid, pw): raise ValueError("bad cookie")
    src._make = bad
    src.resume()
    assert src.state == "need_login" and sent
    db.set_setting("icloud_apple_id", None)


def test_saved_password_store_resume_and_forget():
    """Remember password: 0600 file outside the DB, used on resume, deleted when Apple rejects it."""
    import stat
    from pyicloud.exceptions import PyiCloudFailedLoginException
    from famloc import icloud_source as ics

    sent = []
    class _Alerts:
        def notify(self, *a, **k): sent.append(a)
    src = ics.ICloudSource(cfg, _Alerts())
    ok = type("A", (), {"requires_2fa": False, "requires_2sa": False})()
    seen = []
    src._make = lambda aid, pw: (seen.append(pw), ok)[1]

    src.login("someone@example.com", "hunter2", remember=True)
    assert ics.SECRET_PATH.read_text() == "hunter2"
    assert stat.S_IMODE(ics.SECRET_PATH.stat().st_mode) == 0o600
    assert src.status()["password_saved"] is True
    assert "hunter2" not in json.dumps(db.get_setting("cfg_overrides", {}))  # never in the DB

    src.resume()
    assert seen[-1] == "hunter2" and src.state == "ok"

    tfa = type("A", (), {"requires_2fa": True, "requires_2sa": False})()
    src._make = lambda aid, pw: tfa
    db.set_setting("icloud_need_login_mailed", False)
    src.resume()
    assert src.state == "need_2fa" and src.api is tfa and sent   # keeps the api for the code

    def wrong(aid, pw): raise PyiCloudFailedLoginException("Invalid email/password combination.")
    src._make = wrong
    src.resume()
    assert src.state == "need_login" and not ics.SECRET_PATH.exists()   # never retry a wrong password

    src._make = lambda aid, pw: ok
    src.login("someone@example.com", "hunter2", remember=True)
    src.login("someone@example.com", "hunter2", remember=False)
    assert not ics.SECRET_PATH.exists()
    src.login("someone@example.com", "hunter2", remember=True)
    src.logout()
    assert not ics.SECRET_PATH.exists()
    db.set_setting("icloud_apple_id", None)


def test_rough_fix_alone_never_alerts():
    """2026-11-23: a 427 m cell guess ~1 km out, between 9 m GPS fixes at the place, emailed
    "<person> left <place>". A rough fix now needs confirming; a contradicting fix drops it."""
    db.upsert_device("icloud:rough", "icloud", "Rough phone", owner="Rough")
    dev = db.q1("SELECT * FROM devices WHERE id='icloud:rough'")
    gid = db.x("INSERT INTO geofences(name,lat,lon,radius,on_enter,on_exit,people) VALUES('College',37.7749,-122.4194,300,1,1,'[\"Rough\"]')").lastrowid
    a = Alerter(config.Config(email_enabled=False))
    a.check_fix(dev, 1000, 37.7749, -122.4194, 9)        # inside: initial state
    a.check_fix(dev, 1060, 37.7662, -122.4265, 427)      # rough, ~1.1 km out: waits
    a.check_fix(dev, 1066, 37.7749, -122.4194, 9)        # good, inside: drops it
    assert not db.q("SELECT * FROM events WHERE geofence_id=?", (gid,))
    a.check_fix(dev, 2000, 37.7662, -122.4265, 300)      # a real departure that starts rough...
    assert not db.q("SELECT * FROM events WHERE geofence_id=?", (gid,))
    a.check_fix(dev, 2120, 37.7612, -122.4318, 10)       # ...confirmed by a good fix outside
    ev = db.q("SELECT kind, ts FROM events WHERE geofence_id=?", (gid,))
    assert [(e["kind"], e["ts"]) for e in ev] == [("exit", 2000)], ev   # carries the first sighting
    for t in (3000, 3300, 3601):                         # rough-only coverage: confirmed after 10 min
        a.check_fix(dev, t, 37.7749, -122.4194, 200)
    assert [e["kind"] for e in db.q("SELECT kind FROM events WHERE geofence_id=? ORDER BY ts", (gid,))] == ["exit", "enter"]


def test_tracker_ignores_a_rough_spike():
    from famloc.tracking import Tracker, trusted
    t = Tracker(config.Config(movement_threshold_m=250, follow_rest_minutes=10, rough_accuracy_m=50))
    home = [(1000 + 120 * i, 37.7749, -122.4194, 9) for i in range(5)]
    spike = (1500, 37.7662, -122.4265, 427)
    seq = sorted(home[:4] + [spike])
    assert not t.on_fixes("Sp", seq, 1500)                     # newest is rough and unconfirmed
    seq2 = sorted(home + [spike])
    assert not t.on_fixes("Sp", seq2, 1500 + 200)              # confirmed as an out-and-back jump
    assert spike not in trusted(seq2, 50)
    drive = [(5000 + 60 * i, 37.77 + 0.004 * i, -122.42, 80 if i % 2 else 10) for i in range(6)]
    assert t.on_fixes("Dr", drive, drive[-1][0])               # a real drive with rough fixes still counts



# ---------------------------------------------------------------- v2 user pages
def _client():
    import warnings
    warnings.filterwarnings("ignore")
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from famloc import web
    tracker = SimpleNamespace(status=lambda *a, **k: {"mode": "x"}, family_status=lambda *a: {})
    icloud = SimpleNamespace(tracker=tracker, status=lambda: {}, poke=lambda: None)
    stub = SimpleNamespace(status=lambda: {})
    web._fails.clear()
    app = web.create_app(cfg, icloud, stub, stub, al)
    return TestClient(app), web


def _users_world():
    """Two people with phones and a point each, one main-page place, one user page that shows only Kim."""
    for o, dev, lat in (("Kim", "icloud:kim", 38.0), ("Lee", "icloud:lee", 38.5)):
        db.upsert_device(dev, "icloud", f"{o} phone", owner=o, device_class="iPhone")
        db.add_point(dev, 1000, lat, -120.0, acc=5)
    db.upsert_device("photos:t", "photos", "Photos", owner="Kim", device_class="Photos")
    db.add_point("photos:t", 900, 38.0, -120.0, kind="photo")
    db.x("DELETE FROM viewers"); db.x("DELETE FROM geofences WHERE name LIKE 'U-%'")
    db.x("INSERT INTO geofences(name,lat,lon,radius) VALUES('U-main',38.0,-120.0,150)")


def _main_login(c):
    from famloc import web
    if not db.get_setting("ui_password"):
        c.post("api/auth/setup", json={"password": "testpass123"})
    r = c.post("api/auth/login", json={"password": "testpass123"})
    assert r.status_code == 200, r.text


def test_user_page_link_and_scope():
    c, web = _client()
    _users_world()
    _main_login(c)
    v = c.post("api/viewers", json={"name": "Gran", "email": "gran@example.com", "people": ["Kim"]}).json()
    tok = v["lan_link"].rstrip("/").split("/")[-1]
    assert v["lan_link"].endswith(f"v/{tok}/") and len(tok) >= 30
    u = TestClientNoCookies = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(c.app)
    base = f"v/{tok}/"
    # page: served with relative assets and a referrer policy; the bare path redirects
    r = u.get(f"v/{tok}", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"{tok}/"
    r = u.get(base)
    assert r.status_code == 200 and '"../../static/app.js' in r.text and 'name="referrer"' in r.text
    st = u.get(base + "api/auth/state").json()
    assert st["authed"] and st["viewer"]["name"] == "Gran" and st["viewer"]["people"] == ["Kim"]
    # only Kim, no photos, no tracking state
    assert [d["id"] for d in u.get(base + "api/devices").json()] == ["icloud:kim"]
    assert [p["name"] for p in u.get(base + "api/people").json()] == ["Kim"]
    assert all(p["tracking"] is None for p in u.get(base + "api/people").json())
    pts = u.get(base + "api/points", params={"start": 0, "end": 5000}).json()["points"]
    assert set(pts) == {"icloud:kim"}
    pts = u.get(base + "api/points", params={"start": 0, "end": 5000, "devices": "icloud:lee,photos:t"}).json()["points"]
    assert pts == {}, "naming someone else's device must not leak it"
    assert u.get(base + "api/analysis", params={"device": "icloud:lee", "start": 0, "end": 5000}).status_code == 404
    # closed by default: tracking, settings, accounts, the user list
    for m, path in (("GET", "api/config"), ("PUT", "api/config"), ("POST", "api/icloud/poll"), ("GET", "api/viewers"),
                    ("POST", "api/people/Kim/follow"), ("GET", "api/icloud/locate-log"), ("POST", "api/email/test"),
                    ("GET", "api/digest"), ("POST", "api/auth/change"), ("GET", "metrics")):
        assert u.request(m, base + path).status_code == 403, path
    # own places only, both ways
    assert u.get(base + "api/geofences").json() == []
    gid = u.post(base + "api/geofences", json={"name": "U-gran", "lat": 38.0, "lon": -120.0, "people": ["Kim", "Lee"]}).json()["id"]
    mine = u.get(base + "api/geofences").json()
    assert [g["name"] for g in mine] == ["U-gran"] and mine[0]["people"] == ["Kim"], "people clipped to the page's"
    main_ids = [g["id"] for g in c.get("api/geofences").json()]
    assert gid not in main_ids and any(g["name"] == "U-main" for g in c.get("api/geofences").json())
    main_gid = db.q1("SELECT id FROM geofences WHERE name='U-main'")["id"]
    body = {"name": "x", "lat": 1, "lon": 1}
    assert u.put(base + f"api/geofences/{main_gid}", json=body).status_code == 404
    assert u.delete(base + f"api/geofences/{main_gid}").status_code == 404
    assert c.put(f"api/geofences/{gid}", json=body).status_code == 404, "main page can't edit a user's place by id"
    # daily email switch is the user's; the address is not
    assert u.put(base + "api/me", json={"digest": True, "email": "evil@example.com"}).json()["digest"] == 1
    assert db.viewer(v["id"])["email"] == "gran@example.com"
    # a new link kills the old one; deleting removes their places
    v2 = c.post(f"api/viewers/{v['id']}/new-link").json()
    assert u.get(base + "api/devices").status_code == 404
    tok2 = v2["lan_link"].rstrip("/").split("/")[-1]
    assert u.get(f"v/{tok2}/api/devices").status_code == 200
    assert c.delete(f"api/viewers/{v['id']}").json()["ok"]
    assert not db.q1("SELECT id FROM geofences WHERE id=?", (gid,))


def test_user_page_unknown_links_are_throttled():
    c, web = _client()
    web._bad_links.clear()
    stale = [c.get("v/" + "x" * 32 + "/api/devices").status_code for _ in range(30)]
    assert stale == [404] * 30, "a stale tab retrying one dead link never locks anyone out"
    r = c.get("v/" + "x" * 32 + "/")
    assert r.status_code == 404 and "doesn't work any more" in r.text, "the page itself explains"
    codes = [c.get(f"v/{i:032d}/api/devices").status_code for i in range(10)]
    assert codes == [404] * 9 + [429], codes            # 10 different wrong links: guessing
    assert c.post("api/auth/login", json={"password": "nope-nope"}).status_code == 401, "password lockout is separate"
    web._bad_links.clear(); web._fails.clear()


def test_user_page_alerts_and_daily_email():
    import time as _t
    from famloc.alerts import build_digest, yesterday
    db.upsert_device("icloud:ka", "icloud", "Kai phone", owner="Kai", device_class="iPhone")
    db.upsert_device("icloud:jo", "icloud", "Jo phone", owner="Jo", device_class="iPhone")
    tok = "t" * 32
    db.x("DELETE FROM viewers")
    vid = db.x("INSERT INTO viewers(name,token,email,people,digest) VALUES('Aunt',?, 'aunt@example.com', '[\"Kai\"]', 1)",
               (tok,)).lastrowid
    ug = db.x("INSERT INTO geofences(name,lat,lon,radius,viewer_id) VALUES('Aunt-house',39.0,-121.0,150,?)", (vid,)).lastrowid
    mg = db.x("INSERT INTO geofences(name,lat,lon,radius) VALUES('Main-school',39.0,-121.0,150)").lastrowid
    sent = []
    old = (al.send_email, al.smtp_ready, al.email_configured, cfg.public_url)
    al.send_email = lambda subj, body, viewer=None: sent.append((subj, body, viewer and viewer["email"]))
    al.smtp_ready = al.email_configured = lambda: True
    cfg.public_url = "https://tunnel.example/family/"
    try:
        for dev in ("icloud:ka", "icloud:jo"):
            d = db.q1("SELECT * FROM devices WHERE id=?", (dev,))
            al.check_fix(d, 100, 39.0, -121.0, 5)      # learn: inside both
            al.check_fix(d, 200, 39.02, -121.0, 5)     # leave
        for _ in range(50):
            if len(sent) >= 3: break
            _t.sleep(0.02)
    finally:
        al.send_email, al.smtp_ready, al.email_configured, cfg.public_url = old
    to_aunt = [x for x in sent if x[2] == "aunt@example.com"]
    to_main = [x for x in sent if x[2] is None]
    assert len(to_aunt) == 1 and "Kai left Aunt-house" in to_aunt[0][1], sent      # Jo isn't on her page
    assert f"https://tunnel.example/family/v/{tok}/" in to_aunt[0][1]
    assert "home Wi-Fi" not in to_aunt[0][1] and "#lanes" not in to_aunt[0][1], "no main-page links in a user email"
    assert {x[0] for x in to_main} == {"Kai left Main-school", "Jo left Main-school"}
    assert all("Aunt-house" not in x[1] for x in to_main)
    ev = db.q("SELECT viewer_id, geofence_id FROM events WHERE geofence_id IN (?,?)", (ug, mg))
    assert {(e["viewer_id"], e["geofence_id"]) for e in ev} == {(vid, ug), (None, mg)}
    # daily summaries: the main one ignores her place; hers is yesterday, Kai only, link first
    t0, t1 = yesterday(_t.time())
    assert t1 - t0 in (82800, 86400, 90000) and t1 <= _t.time()
    v = db.viewer(vid)
    cfg.public_url = "https://tunnel.example/family/"
    try:
        _, body = build_digest(cfg, viewer=v)
        _, main = build_digest(cfg)
    finally:
        cfg.public_url = ""
    assert body.startswith(f"Your link for today: https://tunnel.example/family/v/{tok}/")
    assert "Kai" in body and "Jo" not in body and "Main-school" not in body
    assert "Aunt-house" not in main
    assert f"Aunt (sees Kai)\n    https://tunnel.example/family/v/{tok}/" in main, "main summary lists user page links"


def test_every_person_gets_their_own_page():
    from famloc.web import new_token
    db.x("DELETE FROM viewers"); db.set_setting("person_pages_off", [])
    db.upsert_device("icloud:pp1", "icloud", "Pat phone", owner="Pat", device_class="iPhone")
    db.ensure_person_pages(new_token)
    pat = db.q1("SELECT * FROM viewers WHERE auto_owner='Pat'")
    assert pat and json.loads(pat["people"]) == ["Pat"]
    n = db.q1("SELECT COUNT(*) n FROM viewers")["n"]
    db.ensure_person_pages(new_token)
    assert db.q1("SELECT COUNT(*) n FROM viewers")["n"] == n, "never doubles up"
    c, web = _client(); _main_login(c)
    assert c.delete(f"api/viewers/{pat['id']}").json()["ok"]
    db.ensure_person_pages(new_token)
    assert not db.q1("SELECT id FROM viewers WHERE auto_owner='Pat'"), "a deleted automatic page stays deleted"
    db.set_setting("person_pages_off", [])

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)

