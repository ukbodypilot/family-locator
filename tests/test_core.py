"""Run: FAMLOC_DATA=$(mktemp -d) .venv/bin/python -m pytest -q tests  (or plain python)."""
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


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)

