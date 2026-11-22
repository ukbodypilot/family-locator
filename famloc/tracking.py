"""Per-person locate scheduling with a "follow while moving" mode.

Each person (device owner) gets fresh-fix requests on their own interval: their override if set,
else the global `locate_minutes`. When their fixes show movement, they're *followed*: located
every `moving_locate_minutes` until they've stayed put for `follow_rest_minutes`, with a hard cap
of `follow_max_hours`. A manual "follow for N minutes" overrides everything.

Pure logic plus settings reads; no Apple calls here, so it's unit-tested directly.
"""

from __future__ import annotations

import math
import threading

from . import db
from .config import Config


def _dist_m(a1, o1, a2, o2) -> float:
    r = 6371008.8
    p1, p2 = math.radians(a1), math.radians(a2)
    dp, dl = p2 - p1, math.radians(o2 - o1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class Tracker:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.last_sent: dict[str, float] = {}
        self.follow_until: dict[str, float] = {}
        self.follow_started: dict[str, float] = {}
        self.manual_until: dict[str, float] = {}
        self.cap_until: dict[str, float] = {}
        self.sent_count: dict[str, int] = {}
        self.follows_started = 0
        # answer tracking: did a new fix follow each request? Back off people whose phones don't answer
        # (phone unreachable, or Apple quietly not passing requests on).
        self.pending: dict[str, float] = {}
        self.streak: dict[str, int] = {}
        self.answered: dict[str, int] = {}
        self.unanswered: dict[str, int] = {}

    # ------------------------------------------------------------ settings
    @staticmethod
    def person_settings(owner: str) -> dict:
        return (db.get_setting("person_tracking", {}) or {}).get(owner, {}) or {}

    @staticmethod
    def set_person(owner: str, *, locate_minutes="keep", follow="keep") -> None:
        allp = db.get_setting("person_tracking", {}) or {}
        cur = dict(allp.get(owner, {}))
        if locate_minutes != "keep":
            if locate_minutes is None:
                cur.pop("locate_minutes", None)
            else:
                v = int(locate_minutes)
                cur["locate_minutes"] = 0 if v <= 0 else max(1, min(1440, v))
        if follow != "keep":
            if follow is None:
                cur.pop("follow", None)
            else:
                cur["follow"] = bool(follow)
        if cur:
            allp[owner] = cur
        else:
            allp.pop(owner, None)
        db.set_setting("person_tracking", allp)

    def follow_enabled(self, owner: str) -> bool:
        v = self.person_settings(owner).get("follow")
        return bool(self.cfg.follow_moving) if v is None else bool(v)

    def normal_minutes(self, owner: str) -> float:
        v = self.person_settings(owner).get("locate_minutes")
        return float(self.cfg.locate_minutes if v is None else v)

    # ------------------------------------------------------------ state
    def following(self, owner: str, now: float) -> str | None:
        """'manual', 'moving' or None."""
        if self.manual_until.get(owner, 0) > now:
            return "manual"
        if self.follow_enabled(owner) and self.follow_until.get(owner, 0) > now:
            return "moving"
        return None

    ANSWER_WINDOW_S = 180
    BACKOFF_AFTER = 3      # unanswered requests in a row before slowing down
    BACKOFF_CAP_MIN = 60

    def backoff_factor(self, owner: str) -> int:
        k = self.streak.get(owner, 0) - self.BACKOFF_AFTER + 1
        return 1 if k <= 0 else 2 ** min(k, 6)

    def interval_minutes(self, owner: str, now: float) -> float:
        f = self.following(owner, now)
        base = float(self.cfg.moving_locate_minutes) if f else self.normal_minutes(owner)
        if base <= 0 or f == "manual":
            return base
        factor = self.backoff_factor(owner)
        return base if factor == 1 else max(base, min(base * factor, self.BACKOFF_CAP_MIN))

    def note_fix(self, owner: str, fix_ts: float) -> None:
        """A new fix arrived for this person: it answers any request sent shortly before it."""
        with self.lock:
            t = self.pending.get(owner)
            if t is not None and fix_ts >= t - 30:
                self.pending.pop(owner, None)
                self.answered[owner] = self.answered.get(owner, 0) + 1
                self.streak[owner] = 0

    def check_timeouts(self, now: float) -> list[str]:
        """Requests with no new fix within ANSWER_WINDOW_S count as unanswered. Returns those owners."""
        out = []
        with self.lock:
            for o, t in list(self.pending.items()):
                if now - t > self.ANSWER_WINDOW_S:
                    self.pending.pop(o, None)
                    self.unanswered[o] = self.unanswered.get(o, 0) + 1
                    self.streak[o] = self.streak.get(o, 0) + 1
                    out.append(o)
        return out

    def due(self, owners: list[str], now: float) -> list[str]:
        out = []
        with self.lock:
            for i, o in enumerate(owners):
                iv = self.interval_minutes(o, now)
                if iv <= 0:
                    continue
                if o not in self.last_sent:
                    # after a start: first request 2 min in, people 30 s apart, never a burst at startup
                    self.last_sent[o] = now - iv * 60 + 120 + i * 30
                if now - self.last_sent[o] >= iv * 60:
                    out.append(o)
        return out

    def mark_sent(self, owner: str, now: float, requested: bool = True) -> None:
        with self.lock:
            self.last_sent[owner] = now
            if requested:
                self.pending.setdefault(owner, now)
            self.sent_count[owner] = self.sent_count.get(owner, 0) + 1

    def next_due(self, owner: str, now: float) -> float | None:
        iv = self.interval_minutes(owner, now)
        if iv <= 0:
            return None
        return self.last_sent.get(owner, now) + iv * 60

    # ------------------------------------------------------------ movement
    def _need(self, acc_a, acc_b) -> float:
        # beyond the threshold AND the two fixes' combined accuracy, so GPS wobble doesn't count
        return max(self.cfg.movement_threshold_m, (acc_a or 0) + (acc_b or 0))

    def moving(self, pts: list[tuple]) -> bool:
        """pts: recent (ts, lat, lon, acc) for one person, oldest first. Moving if the newest fix is
        clearly far from the fix before it (when that one is at most 35 minutes older)."""
        if len(pts) < 2:
            return False
        (ts_p, la_p, lo_p, acc_p), (ts_n, la_n, lo_n, acc_n) = pts[-2], pts[-1]
        if not (0 < ts_n - ts_p <= 35 * 60):
            return False
        return _dist_m(la_p, lo_p, la_n, lo_n) > self._need(acc_p, acc_n)

    def at_rest(self, pts: list[tuple]) -> bool:
        """Every fix in the last `follow_rest_minutes` is close to the newest, and those fixes
        really cover that span (not just two fixes a minute apart)."""
        if not pts:
            return False
        ts_n, la_n, lo_n, acc_n = pts[-1]
        window = self.cfg.follow_rest_minutes * 60
        recent = [p for p in pts if ts_n - p[0] <= window]
        if ts_n - recent[0][0] < window * 0.8:
            return False
        return all(_dist_m(la, lo, la_n, lo_n) <= self._need(acc, acc_n) for _, la, lo, acc in recent)

    def moved_recently(self, pts: list[tuple], since: float) -> bool:
        """Any consecutive pair of this device's fixes, ending after `since`, that clearly moved."""
        for a, b in zip(pts, pts[1:]):
            if b[0] > since and 0 < b[0] - a[0] <= 35 * 60 and _dist_m(a[1], a[2], b[1], b[2]) > self._need(a[3], b[3]):
                return True
        return False

    def on_fixes(self, owner: str, seqs, now: float) -> bool:
        """Call after new fixes arrive for a person. `seqs` is one oldest-first (ts, lat, lon, acc)
        list per device; a single list is accepted too. Each device is only compared with its own
        fixes, so an iPad at home and a phone in a pocket never look like a jump. Starts following
        when any device moves; stops when a device has a full rest window and none moved in it.
        Returns True while following because of movement."""
        if seqs and isinstance(seqs[0], tuple):
            seqs = [seqs]
        seqs = [q for q in seqs if q]
        if not self.follow_enabled(owner):
            return False
        window = self.cfg.follow_rest_minutes * 60
        with self.lock:
            following = self.follow_until.get(owner, 0) > now
            if following and now - self.follow_started.get(owner, now) > self.cfg.follow_max_hours * 3600:
                # runaway guard (e.g. noisy fixes): stop, and don't restart for an hour
                self.follow_until[owner] = 0
                self.cap_until[owner] = now + 3600
                return False
            if following:
                newest = max((q[-1][0] for q in seqs), default=now)
                moved = any(self.moved_recently(q, newest - window) for q in seqs)
                if not moved and any(self.at_rest(q) for q in seqs):
                    self.follow_until[owner] = 0
                    return False
                self.follow_until[owner] = now + window  # expires on its own if fixes stop coming
                return True
            if now < self.cap_until.get(owner, 0) or not any(self.moving(q) for q in seqs):
                return False
            self.follow_started[owner] = now
            self.follows_started += 1
            self.follow_until[owner] = now + window
            # the first fast locate is due straight away
            self.last_sent[owner] = min(self.last_sent.get(owner, now), now - self.cfg.moving_locate_minutes * 60)
            return True

    def start_manual(self, owner: str, minutes: float, now: float) -> None:
        with self.lock:
            if minutes <= 0:
                self.manual_until.pop(owner, None)
                self.follow_until[owner] = 0
            else:
                self.manual_until[owner] = now + minutes * 60
                self.last_sent[owner] = now - self.cfg.moving_locate_minutes * 60  # locate right away

    def status(self, owner: str, now: float) -> dict:
        f = self.following(owner, now)
        ps = self.person_settings(owner)
        return {
            "locate_minutes": ps.get("locate_minutes"), "follow": ps.get("follow"),
            "effective_minutes": self.interval_minutes(owner, now),
            "follow_enabled": self.follow_enabled(owner),
            "following": f, "following_until": (self.manual_until.get(owner) if f == "manual" else self.follow_until.get(owner)) if f else None,
            "next_locate": self.next_due(owner, now), "locates_sent": self.sent_count.get(owner, 0),
            "answered": self.answered.get(owner, 0), "unanswered": self.unanswered.get(owner, 0),
            "unanswered_streak": self.streak.get(owner, 0), "backoff": self.backoff_factor(owner),
        }
