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


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
