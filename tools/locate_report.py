"""Summarise the locate governor's event log, for tuning the request strategy from data.

    .venv/bin/python tools/locate_report.py                 # last 2 hours
    .venv/bin/python tools/locate_report.py --since 16:15   # today from 16:15
    .venv/bin/python tools/locate_report.py --hours 24 --events   # also list every event

Reads data/famloc.db (or $FAMLOC_DATA). People are shown by initial.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("FAMLOC_DATA", ROOT / "data")) / "famloc.db"


def ini(p: str | None) -> str:
    return "ALL" if p == "ALL" else (p or "?")[:1] + "…"


def fmt(t: float) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%H:%M:%S")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="HH:MM today (local)")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--events", action="store_true", help="list every event")
    a = ap.parse_args()
    now = time.time()
    if a.since:
        h, m = map(int, a.since.split(":"))
        t0 = dt.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
    else:
        t0 = now - a.hours * 3600
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = [(ts, ev, p, json.loads(d) if d else {}) for ts, ev, p, d in
            c.execute("SELECT ts, event, person, data FROM locate_events WHERE ts>=? ORDER BY ts", (t0,))]
    print(f"Locate events {fmt(t0)} -> {fmt(now)}  ({len(rows)} events)\n")
    if not rows:
        sys.exit(0)

    people = sorted({p for _, _, p, _ in rows if p})
    stat = defaultdict(Counter)
    lat = defaultdict(list)
    for ts, ev, p, d in rows:
        s = stat[p]
        s[ev] += 1
        if ev == "reply":
            s["isLocating_true" if d.get("isLocating") else "isLocating_false" if d.get("isLocating") is False else "isLocating_none"] += 1
        if ev == "request":
            s["why_" + str(d.get("why"))] += 1
        if ev == "answer":
            lat[p].append(d.get("latency_s", 0))

    print("Per person")
    print(f"{'who':5} {'req':>4} {'locTrue':>7} {'locFalse':>8} {'answered':>8} {'unans':>5} {'selfFix':>7} {'med.lat':>7}  why")
    for p in people:
        s = stat[p]
        whys = ", ".join(f"{k[4:]}={v}" for k, v in s.items() if k.startswith("why_"))
        ml = f"{statistics.median(lat[p]):.0f}s" if lat[p] else "-"
        print(f"{ini(p):5} {s['request']:>4} {s['isLocating_true']:>7} {s['isLocating_false']:>8} {s['answer']:>8} "
              f"{s['unanswered']:>5} {s['fix']:>7} {ml:>7}  {whys}")
    print("  req = requests sent; locTrue/locFalse = Apple's isLocating on the reply; answered = new fix")
    print("  within 3 min of a request; selfFix = new fix with no request pending (phone reported by itself)\n")

    gov = [(ts, ev, p, d) for ts, ev, p, d in rows if ev in ("pause", "resume", "blocked", "unblocked", "backoff", "follow_start", "follow_stop", "settings", "reinit", "ctx_change")]
    if gov:
        print("Governor and follow decisions")
        for ts, ev, p, d in gov:
            print(f"  {fmt(ts)}  {ev:12} {ini(p):5} " + " ".join(f"{k}={fmt(v) if k == 'until' and v else v}" for k, v in d.items() if k != "self_owner"))
        print()

    print("Family requests per 10 min  (r = request, T/F = isLocating, a = answered, s = self-reported fix)")
    b0 = int(t0 // 600 * 600)
    for b in range(b0, int(now) + 1, 600):
        inb = [(ev, p, d) for ts, ev, p, d in rows if b <= ts < b + 600 and p and not d.get("self_owner")]
        r = sum(1 for ev, p, d in inb if ev == "request")
        tr = sum(1 for ev, p, d in inb if ev == "reply" and d.get("isLocating"))
        fa = sum(1 for ev, p, d in inb if ev == "reply" and d.get("isLocating") is False)
        an = sum(1 for ev, p, d in inb if ev == "answer")
        sf = sum(1 for ev, p, d in inb if ev == "fix")
        print(f"  {fmt(b)[:5]}  r={r:<3} T={tr:<3} F={fa:<3} a={an:<3} s={sf:<3} " + "#" * r)

    if a.events:
        print("\nAll events")
        for ts, ev, p, d in rows:
            print(f"  {fmt(ts)}  {ev:12} {ini(p):5} {json.dumps(d)}")


if __name__ == "__main__":
    main()
