"""Change settings on the live app without a restart, with a reason recorded.

    .venv/bin/python tools/tune.py                                   # show current tunables
    .venv/bin/python tools/tune.py family_locates_per_hour=30 --why "40 requests, 0 ignored"

Writes the DB setting the app re-reads every minute (same store as the Setup tab), and appends
the change and its reason to data/tuning_log.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from famloc import config  # noqa: E402

DATA = Path(os.environ.get("FAMLOC_DATA", ROOT / "data"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("changes", nargs="*", help="key=value")
    ap.add_argument("--why", default="")
    a = ap.parse_args()
    c = sqlite3.connect(DATA / "famloc.db", timeout=30)
    row = c.execute("SELECT value FROM settings WHERE key='cfg_overrides'").fetchone()
    ov = json.loads(row[0]) if row else {}
    cfg = config.load()
    config.apply_overrides(cfg, ov)
    if not a.changes:
        for k, (label, lo, hi, unit) in config.EDITABLE.items():
            print(f"{k:28} {getattr(cfg, k)!s:>8} {unit:4} [{lo}..{hi}]  {label}{'  (overridden)' if k in ov else ''}")
        return
    new = {}
    for ch in a.changes:
        k, v = ch.split("=", 1)
        if k not in config.EDITABLE:
            sys.exit(f"unknown setting {k}")
        new[k] = float(v)
    clean = config.apply_overrides(cfg, new)
    before = {k: ov.get(k, getattr(config.Config(), k)) for k in clean}
    ov.update(clean)
    c.execute("INSERT INTO settings(key,value) VALUES('cfg_overrides',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (json.dumps(ov),))
    c.commit()
    line = f"- {dt.datetime.now():%Y-%m-%d %H:%M}  " + ", ".join(f"`{k}` {before[k]} -> {v}" for k, v in clean.items()) + (f". {a.why}" if a.why else "")
    log = DATA / "tuning_log.md"
    if not log.exists():
        log.write_text("# Locate governor tuning log\n\nEach change: when, what, and the evidence for it.\n\n")
    with open(log, "a") as f:
        f.write(line + "\n")
    print(line)
    print("Applies within a minute (no restart).")


if __name__ == "__main__":
    main()
