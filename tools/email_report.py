"""Email a locate-governor progress report: measurements + current model + settings + tuning log.

    .venv/bin/python tools/email_report.py --until "2026-11-21 23:00" [--since 16:25] [--dry-run]

Run by famloc-report.timer every 30 minutes during a tuning session. Exits quietly after --until.
The "model" section comes from data/tuning_model.md (written during tuning runs) plus indicators
computed here from the locate_events table.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from famloc import config  # noqa: E402
from famloc.alerts import Alerter  # noqa: E402

DATA = Path(os.environ.get("FAMLOC_DATA", ROOT / "data"))
GOV_KEYS = ["locate_minutes", "moving_locate_minutes", "follow_moving", "family_locates_per_hour", "family_pause_minutes",
            "family_throttle_after", "family_pause_cap_minutes", "locate_spacing_s", "unanswered_backoff_after"]


def indicators(c, t0: float, t1: float) -> dict:
    rows = [(ts, ev, p, json.loads(d) if d else {}) for ts, ev, p, d in
            c.execute("SELECT ts,event,person,data FROM locate_events WHERE ts BETWEEN ? AND ? ORDER BY ts", (t0, t1))]
    fam = [r for r in rows if r[2] and r[2] != "ALL" and not r[3].get("self_owner")]
    me = [r for r in rows if r[3].get("self_owner")]
    def cnt(rs, ev, cond=lambda d: True):
        return sum(1 for _, e, _, d in rs if e == ev and cond(d))
    lat = [d.get("latency_s", 0) for _, e, _, d in fam if e == "answer"]
    return {
        "family_requests": cnt(fam, "request"), "family_isLocating_true": cnt(fam, "reply", lambda d: d.get("isLocating") is True),
        "family_isLocating_false": cnt(fam, "reply", lambda d: d.get("isLocating") is False),
        "family_answered": cnt(fam, "answer"), "family_unanswered": cnt(fam, "unanswered"), "family_self_fixes": cnt(fam, "fix"),
        "family_median_answer_s": round(statistics.median(lat)) if lat else None,
        "own_requests": cnt(me, "request"), "own_isLocating_true": cnt(me, "reply", lambda d: d.get("isLocating") is True),
        "own_answered": cnt(me, "answer"), "own_self_fixes": cnt(me, "fix"),
        "pauses": [(dt.datetime.fromtimestamp(ts).strftime("%H:%M"), d.get("level"), d.get("minutes"), d.get("trigger"))
                   for ts, e, _, d in rows if e == "pause"],
        "resumes": sum(1 for _, e, _, _ in rows if e == "resume"),
        "blocked_budget": cnt(rows, "blocked", lambda d: d.get("reason") == "budget"),
        "blocked_paused": cnt(rows, "blocked", lambda d: d.get("reason") == "paused"),
    }


def verdict(ind: dict) -> str:
    t, f = ind["family_isLocating_true"], ind["family_isLocating_false"]
    if t + f < 5:
        return "Not enough family replies yet to judge."
    share = t / (t + f)
    if t == 0:
        return (f"Apple has not acted on ANY of {f} family requests (isLocating=false every time) while own-device requests "
                f"{'work' if ind['own_isLocating_true'] else 'are untested'}. Leaning to: single-device locates don't work for "
                f"family members; their fixes come from the phones reporting by themselves ({ind['family_self_fixes']} self-reported fixes).")
    if ind["pauses"]:
        return f"Apple acts on {share:.0%} of family requests, with pause(s) {ind['pauses']}: consistent with a rate limit."
    return f"Apple acts on {share:.0%} of family requests and no pause so far: the budget is under any limit; room to loosen."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", required=True, help="stop sending after this local time, 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--since", default="16:25")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    now = time.time()
    if now > dt.datetime.strptime(a.until, "%Y-%m-%d %H:%M").timestamp():
        return
    h, m = map(int, a.since.split(":"))
    t_since = dt.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
    c = sqlite3.connect(f"file:{DATA / 'famloc.db'}?mode=ro", uri=True)
    last = indicators(c, now - 1800, now)
    total = indicators(c, t_since, now)
    cfg = config.load()
    row = c.execute("SELECT value FROM settings WHERE key='cfg_overrides'").fetchone()
    config.apply_overrides(cfg, json.loads(row[0]) if row else {})
    gov = json.loads((c.execute("SELECT value FROM settings WHERE key='locate_governor'").fetchone() or ["{}"])[0])

    def block(title, ind):
        return "\n".join([title,
            f"  Family: {ind['family_requests']} requests, Apple acted on {ind['family_isLocating_true']} (isLocating=true), "
            f"ignored {ind['family_isLocating_false']}; answered {ind['family_answered']}, unanswered {ind['family_unanswered']}, "
            f"median answer {ind['family_median_answer_s'] or '-'} s; self-reported fixes {ind['family_self_fixes']}",
            f"  Own devices: {ind['own_requests']} requests, acted on {ind['own_isLocating_true']}, answered {ind['own_answered']}, self-reported {ind['own_self_fixes']}",
            f"  Pauses: {ind['pauses'] or 'none'}; resumes {ind['resumes']}; blocked by budget {ind['blocked_budget']}x, by pause {ind['blocked_paused']}x"])

    model_notes = (DATA / "tuning_model.md").read_text() if (DATA / "tuning_model.md").exists() else "(no notes yet)"
    tlog = (DATA / "tuning_log.md").read_text().strip().splitlines()[3:] if (DATA / "tuning_log.md").exists() else []
    report = subprocess.run([sys.executable, str(ROOT / "tools/locate_report.py"), "--hours", "0.5"],
                            capture_output=True, text=True, cwd=ROOT).stdout
    paused_until = gov.get("until", 0)
    body = "\n\n".join([
        block("MEASUREMENTS, last 30 min", last),
        block(f"MEASUREMENTS, since {a.since}", total),
        "MODEL\n  Auto verdict (since %s): %s\n\n%s" % (a.since, verdict(total), model_notes.strip()),
        "GOVERNOR NOW\n  " + ", ".join(f"{k}={getattr(cfg, k)}" for k in GOV_KEYS) +
        f"\n  Family pause: {'until ' + dt.datetime.fromtimestamp(paused_until).strftime('%H:%M') + ' (level ' + str(gov.get('level')) + ')' if paused_until > now else 'none'}",
        "TUNING LOG\n" + ("\n".join(tlog) if tlog else "  no changes yet"),
        "DETAIL (last 30 min)\n" + report.strip(),
    ])
    t, f = total["family_isLocating_true"], total["family_isLocating_false"]
    subject = f"Locate tuning {dt.datetime.now():%H:%M}: family acted-on {t}/{t + f}, pauses {len(total['pauses'])}"
    if a.dry_run:
        print(subject + "\n\n" + body)
        return
    err = Alerter(cfg).send_email(subject, body)
    print("sent" if err is None else f"FAILED: {err}")


if __name__ == "__main__":
    main()
