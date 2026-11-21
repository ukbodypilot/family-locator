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
db.upsert_device("photos:icloud", "photos", "iCloud Photos", owner="Alex", model="Photo GPS", device_class="Photos")
for i in range(300):
    p = random.choice(list(PLACES.values()))
    db.add_point("photos:icloud", now - random.uniform(0, 400 * 86400), p[0] + random.gauss(0, 0.01), p[1] + random.gauss(0, 0.01), kind="photo")
db.x("INSERT INTO geofences(name,lat,lon,radius,color,on_enter,on_exit,devices) VALUES('Home',?,?,150,'#16a34a',1,1,'[]')", HOME)
db.x("INSERT INTO geofences(name,lat,lon,radius,color,on_enter,on_exit,devices) VALUES('School',?,?,200,'#d97706',1,1,'[]')", PLACES["School"])
print(db.q1("SELECT COUNT(*) n FROM points"))
