"""Fill a THROWAWAY data dir with synthetic devices/tracks for UI testing.

    FAMLOC_DATA=~/famloc-scratch/data .venv/bin/python tools/seed_demo.py
Refuses to run against the real data/ directory.
"""

import math
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from famloc import config, db  # noqa: E402

if Path(config.DATA_DIR).resolve() == (config.ROOT / "data").resolve():
    sys.exit("refusing to seed the real data dir; set FAMLOC_DATA")

db.init()
random.seed(7)
HOME = (37.7749, -122.4194)
PLACES = {"Home": HOME, "School": (37.7599, -122.4148), "Work": (37.7897, -122.3972),
          "Gym": (37.7685, -122.4330), "Park": (37.7694, -122.4862)}
now = time.time()


def walk(dev_id, sched, step_s=300):
    t = now - 14 * 86400
    pos = HOME
    while t < now:
        hour = time.localtime(t).tm_hour
        target = PLACES[sched(hour, time.localtime(t).tm_wday)]
        d = math.dist(pos, target)
        if d > 0.0008:
            f = min(1, 0.006 / d)
            pos = (pos[0] + (target[0] - pos[0]) * f, pos[1] + (target[1] - pos[1]) * f)
        jitter = (random.gauss(0, 0.0002), random.gauss(0, 0.0002))
        db.add_point(dev_id, t, pos[0] + jitter[0], pos[1] + jitter[1], acc=random.choice([5, 10, 35, 65]),
                     battery=0.3 + 0.7 * ((t / 3600) % 24) / 24, kind="GPS")
        t += step_s + random.randint(-60, 60)


db.upsert_device("icloud:demo1", "icloud", "Alex's iPhone", owner="Alex", model="iPhone 16", device_class="iPhone", battery=0.81)
db.upsert_device("icloud:demo2", "icloud", "Sam's iPhone", owner="Sam", model="iPhone 15", device_class="iPhone", battery=0.12, battery_status="Charging")
db.upsert_device("icloud:demo3", "icloud", "Sam's Watch", owner="Sam", model="Apple Watch", device_class="Watch", battery=0.55)
walk("icloud:demo1", lambda h, wd: "Work" if 9 <= h < 17 and wd < 5 else ("Gym" if h == 18 else "Home"))
walk("icloud:demo2", lambda h, wd: "School" if 8 <= h < 15 and wd < 5 else ("Park" if 15 <= h < 17 else "Home"))
walk("icloud:demo3", lambda h, wd: "School" if 8 <= h < 15 and wd < 5 else "Home", step_s=900)
# Trips inside the last 24 h on real road geometry (tools/demo_routes.json: routes from
# routing.openstreetmap.de, map data (c) OpenStreetMap contributors, ODbL), so
# the speed-coloured tracks show every band: city streets, freeway, a bike ride and walks.
import json  # noqa: E402

ROUTES = json.loads((Path(__file__).with_name("demo_routes.json")).read_text())


def follow(route, t0, pace, step_s=60, reverse=False):
    """Fixes every ~step_s along route ([lat, lon, t]); pace(mph_at_free_flow) -> multiplier."""
    pts = route[::-1] if reverse else route
    if reverse:
        end = route[-1][2]
        pts = [[la, lo, end - t] for la, lo, t in pts]
    # retime each segment by the pace function (OSRM free-flow speed in, demo speed out)
    out, tt = [(pts[0][0], pts[0][1], 0.0)], 0.0
    for (a, b, ta), (c, d, tb) in zip(pts, pts[1:]):
        dt = max(tb - ta, 0.01)
        mph = math.dist((a, b), (c, d)) * 111_000 / dt * 2.2369
        tt += dt / pace(mph)
        out.append((c, d, tt))
    fixes, k, t = [], 0, 0.0
    while t <= out[-1][2]:
        while out[k + 1][2] < t:
            k += 1
        (a, b, ta), (c, d, tb) = out[k], out[k + 1]
        f = 0 if tb == ta else (t - ta) / (tb - ta)
        fixes.append((t0 + t, a + (c - a) * f, b + (d - b) * f))
        t += step_s + random.randint(-8, 8)
    return fixes, t0 + out[-1][2]


def wander(centre, t0, minutes, mph=2.4, step_s=60):
    fixes, pos, hdg, t = [], centre, random.uniform(0, 6.28), t0
    while t < t0 + minutes * 60:
        hdg += random.gauss(0, 0.6)
        if math.dist(pos, centre) > 0.002:
            hdg = math.atan2(centre[0] - pos[0], centre[1] - pos[1])
        step = mph / 2.2369 * step_s / 111_000
        pos = (pos[0] + step * math.sin(hdg), pos[1] + step * math.cos(hdg))
        fixes.append((t, *pos)); t += step_s + random.randint(-5, 5)
    return fixes, t


def lay(dev_ids, fixes):
    """Replace everything from the trip's start until now: the trip, then at home (no straight-line
    schedule walks across town after it)."""
    lo, hi = fixes[0][0], fixes[-1][0]
    t, rest = hi + 300, []
    while t < now:
        rest.append((t, HOME[0] + random.gauss(0, 0.0001), HOME[1] + random.gauss(0, 0.0001))); t += 300 + random.randint(-30, 30)
    fixes = fixes + rest
    for dev in dev_ids:
        db.x("DELETE FROM points WHERE device_id=? AND ts > ?", (dev, lo - 600))
        for t, la, lon in fixes:
            if t < now:
                db.add_point(dev, t, la + random.gauss(0, 0.00003), lon + random.gauss(0, 0.00003),
                             acc=random.choice([5, 5, 10, 16]), kind="GPS")


def car(mph):   # free-flow -> demo: a bit of traffic in town, quick on the freeway
    return 1.45 if mph > 50 else (0.8 if mph > 30 else 0.55) * random.uniform(0.8, 1.2)


lt = time.localtime(now)                              # trips in daylight: the last 10:00 that leaves room
day10 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 10, 0, 0, 0, 0, -1))
if now - day10 < 7 * 3600:
    day10 -= 86400
t = day10                                            # Alex: SF -> Stanford, a walk, lunch, home
out, t = follow(ROUTES["drive"], t, car)
walk1, t2 = follow(ROUTES["walk"], t + 900, lambda mph: 1.0)
stay, _ = wander(tuple(ROUTES["walk"][0][:2]), t2, 110, mph=0.4, step_s=300)
back, t3 = follow(ROUTES["drive"], t2 + 112 * 60, car, reverse=True)
lay(["icloud:demo1"], out + walk1 + stay + back)
t = day10 + 3.5 * 3600                               # Sam: bike to the park, walk about, bike back
ride, t = follow(ROUTES["bike"], t, lambda mph: 1.4 * random.uniform(0.85, 1.15))
park, t = wander(tuple(ROUTES["bike"][-1][:2]), t, 60)
home, _ = follow(ROUTES["bike"], t, lambda mph: 1.3 * random.uniform(0.85, 1.15), reverse=True)
lay(["icloud:demo2", "icloud:demo3"], ride + park + home)

db.upsert_device("photos:icloud", "photos", "iCloud Photos", owner="Alex", model="Photo GPS", device_class="Photos")
for i in range(300):
    p = random.choice(list(PLACES.values()))
    db.add_point("photos:icloud", now - random.uniform(0, 400 * 86400), p[0] + random.gauss(0, 0.01), p[1] + random.gauss(0, 0.01), kind="photo")
PLACES["Stanford"] = tuple(ROUTES["walk"][0][:2])
for name, color, r in (("Home", "#577f5b", 150), ("School", "#b7791f", 200), ("Work", "#3f7fa3", 250),
                       ("Gym", "#8a5a9e", 120), ("Park", "#4f8a3c", 400), ("Stanford", "#a8432f", 600)):
    la, lo = PLACES[name]
    db.x("INSERT INTO geofences(name,lat,lon,radius,color,on_enter,on_exit,devices,people) VALUES(?,?,?,?,?,1,1,'[]','[]')",
         (name, la, lo, r, color))

# A user page (v2): Grandma sees Sam only, with her own place. The link is fixed so screenshots can use it.
db.x("DELETE FROM viewers WHERE name='Grandma'")
vid = db.x("INSERT INTO viewers(name,token,email,people,digest,digest_hour,created) VALUES(?,?,?,?,1,8,?)",
           ("Grandma", "demo-grandma-link-0123456789abcdef", "grandma@example.com", '["Sam"]', now - 3 * 86400)).lastrowid
db.x("INSERT INTO geofences(name,lat,lon,radius,color,on_enter,on_exit,devices,people,viewer_id) "
     "VALUES('School',?,?,200,'#b7791f',1,1,'[]','[\"Sam\"]',?)", (*PLACES["School"], vid))
db.x("INSERT INTO geofences(name,lat,lon,radius,color,on_enter,on_exit,devices,people,viewer_id) "
     "VALUES('Park',?,?,400,'#4f8a3c',1,1,'[]','[\"Sam\"]',?)", (*PLACES["Park"], vid))

# Replay the last two days through the real alert logic (email off) so the activity log has entries.
from famloc.alerts import Alerter  # noqa: E402
al = Alerter(config.Config(email_enabled=False))
for dev in db.q("SELECT * FROM devices WHERE source='icloud'"):
    for pt in db.q("SELECT ts,lat,lon,acc FROM points WHERE device_id=? AND ts>? ORDER BY ts", (dev["id"], now - 2 * 86400)):
        al.check_fix(dev, pt["ts"], pt["lat"], pt["lon"], pt["acc"])
print(db.q1("SELECT COUNT(*) n FROM points"), db.q1("SELECT COUNT(*) n FROM events"))
