"""Per-person locate scheduling with a "follow while moving" mode, under a family-wide governor.

Each person (device owner) gets fresh-fix requests on their own interval: their override if set,
else the global `locate_minutes`. When their fixes show movement, they're *followed*: located
every `moving_locate_minutes` until they've stayed put for `follow_rest_minutes`, with a hard cap
of `follow_max_hours`. A manual "follow for N minutes" overrides everything.

A family-wide governor sits on top: Apple appears to limit active locates of *other family members'*
devices per account, not per device. So all requests to family members share one hourly budget,
any two requests are at least 20 s apart, and when Apple stops acting on them (replies with
isLocating=false) all family requests pause, then probe, with the pause doubling each time. The
account holder's own devices are exempt from the budget and pauses. Governor state is saved in the
database so a restart can't reset it.

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
        self.follow_device: dict[str, str] = {}   # the device actually moving: the one fast locates go to
        self.sent_count: dict[str, int] = {}
        self.follows_started = 0
        # answer tracking: did a new fix follow each request? Back off people whose phones don't answer
        # (phone unreachable, or Apple quietly not passing requests on).
        self.pending: dict[str, float] = {}
        self.streak: dict[str, int] = {}
        self.answered: dict[str, int] = {}
        self.unanswered: dict[str, int] = {}
        self.self_owner: str | None = None   # the signed-in Apple ID's own name (exempt from the family budget)
        g = db.get_setting("locate_governor", {}) or {}
        self._blocked: dict[str, str] = {}   # person -> last logged block reason (log changes only)
        self.gov = {"history": [tuple(x) for x in g.get("history", [])], "until": float(g.get("until", 0)),
                    "level": int(g.get("level", 0)), "false_streak": int(g.get("false_streak", 0)),
                    "reason": g.get("reason", "")}

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
        k = self.streak.get(owner, 0) - int(self.cfg.unanswered_backoff_after) + 1
        return 1 if k <= 0 else 2 ** min(k, 6)

    def interval_minutes(self, owner: str, now: float) -> float:
        f = self.following(owner, now)
        base = float(self.cfg.moving_locate_minutes) if f else self.normal_minutes(owner)
        if base <= 0 or f == "manual":
            return base
        factor = self.backoff_factor(owner)
        return base if factor == 1 else max(base, min(base * factor, self.BACKOFF_CAP_MIN))

    def note_fix(self, owner: str, fix_ts: float, device_kind: str | None = None) -> None:
        """A new fix arrived for this person: it answers any request sent shortly before it.
        Fixes with no request pending are logged as 'fix' (the phone reported by itself)."""
        with self.lock:
            t = self.pending.get(owner)
            if t is not None and t - 30 <= fix_ts <= t + self.ANSWER_WINDOW_S:
                self.pending.pop(owner, None)
                self.answered[owner] = self.answered.get(owner, 0) + 1
                was = self.streak.get(owner, 0)
                self.streak[owner] = 0
                db.log_locate_event("answer", owner, latency_s=round(fix_ts - t, 1), kind=device_kind,
                                    self_owner=self.is_self(owner), cleared_streak=was)
            else:
                db.log_locate_event("fix", owner, kind=device_kind, self_owner=self.is_self(owner),
                                    since_last_request_s=round(fix_ts - self.last_sent[owner], 1) if owner in self.last_sent else None)

    def check_timeouts(self, now: float) -> list[str]:
        """Requests with no new fix within ANSWER_WINDOW_S count as unanswered. Returns those owners."""
        out = []
        with self.lock:
            for o, t in list(self.pending.items()):
                if now - t > self.ANSWER_WINDOW_S:
                    self.pending.pop(o, None)
                    self.unanswered[o] = self.unanswered.get(o, 0) + 1
                    before = self.backoff_factor(o)
                    self.streak[o] = self.streak.get(o, 0) + 1
                    out.append(o)
                    db.log_locate_event("unanswered", o, streak=self.streak[o], self_owner=self.is_self(o))
                    if self.backoff_factor(o) != before:
                        db.log_locate_event("backoff", o, factor=self.backoff_factor(o), streak=self.streak[o],
                                            interval_min=self.interval_minutes(o, now))
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

    # ------------------------------------------------------------ family governor
    MIN_SPACING_S = 20
    THROTTLE_AFTER = 3     # family replies in a row with isLocating=false before pausing
    PAUSE_CAP_MIN = 120

    def _save_gov(self) -> None:
        db.set_setting("locate_governor", {**self.gov, "history": [list(x) for x in self.gov["history"]]})

    def is_self(self, owner: str | None) -> bool:
        return bool(owner) and owner == self.self_owner

    def _prune(self, now: float) -> None:
        self.gov["history"] = [h for h in self.gov["history"] if now - h[0] < 3600]

    def family_used(self, now: float) -> int:
        return sum(1 for t, o in self.gov["history"] if now - t < 3600 and not self.is_self(o))

    def family_paused(self, now: float) -> bool:
        return self.gov["until"] > now

    def select(self, due: list[str], now: float) -> list[str]:
        """From the people who are due, pick at most one to locate now: spacing first, then the
        account holder (exempt), then family members within the budget and outside any pause,
        manual follows before movement before normal schedules, longest-waiting first."""
        with self.lock:
            self._prune(now)
            last_any = max((t for t, _ in self.gov["history"]), default=0)
            if now - last_any < self.cfg.locate_spacing_s:
                return []
            rank = {"manual": 0, "moving": 1, None: 2}
            for o in sorted(due, key=lambda o: (rank[self.following(o, now)], self.last_sent.get(o, 0))):
                if self.is_self(o):
                    self._unblock(o, now)
                    return [o]
                if self.family_paused(now):
                    self._block(o, "paused", now, until=self.gov["until"], level=self.gov["level"])
                    continue
                if self.family_used(now) >= self.cfg.family_locates_per_hour:
                    self._block(o, "budget", now, used=self.family_used(now), budget=self.cfg.family_locates_per_hour)
                    continue
                self._unblock(o, now)
                return [o]
            return []

    def _block(self, o: str, reason: str, now: float, **data) -> None:
        if self._blocked.get(o) != reason:
            self._blocked[o] = reason
            db.log_locate_event("blocked", o, now, reason=reason, following=self.following(o, now), **data)

    def _unblock(self, o: str, now: float) -> None:
        if self._blocked.pop(o, None):
            db.log_locate_event("unblocked", o, now)

    def record_request(self, owner: str, now: float, why: str | None = None, **data) -> None:
        with self.lock:
            self._prune(now)
            probe = not self.is_self(owner) and owner != "ALL" and self.gov["level"] > 0
            self.gov["history"].append((now, owner))
            self._save_gov()
            db.log_locate_event("request", owner, now, why=why or ("probe" if probe else self.following(owner, now) or "schedule"),
                                probe=probe, interval_min=self.interval_minutes(owner, now) if owner != "ALL" else None,
                                family_used=self.family_used(now), budget=self.cfg.family_locates_per_hour,
                                pause_level=self.gov["level"], self_owner=self.is_self(owner), **data)

    def record_reply(self, owner: str | None, is_locating, now: float) -> None:
        """Apple's isLocating on a family member's request is the throttle signal: false N times in a
        row pauses all family requests; any false reply while recovering (the probe) doubles the
        pause; a true reply clears everything."""
        if self.is_self(owner) or is_locating is None or owner == "ALL":
            return
        with self.lock:
            g = self.gov
            if is_locating:
                if g["level"] > 0 or g["until"] > now:
                    db.log_locate_event("resume", owner, now, was_level=g["level"])
                g.update(false_streak=0, level=0, until=0.0, reason="")
            else:
                g["false_streak"] += 1
                if g["false_streak"] >= int(self.cfg.family_throttle_after) or g["level"] > 0:
                    trigger = "probe_failed" if g["level"] > 0 else f"{g['false_streak']}_false_in_a_row"
                    g["level"] = min(g["level"] + 1, 6)
                    mins = min(self.cfg.family_pause_minutes * 2 ** (g["level"] - 1), self.cfg.family_pause_cap_minutes)
                    g["until"] = now + mins * 60
                    g["reason"] = f"Apple didn't act on the last {g['false_streak'] or 1} family request(s)"
                    db.log_locate_event("pause", owner, now, level=g["level"], minutes=mins, trigger=trigger,
                                        family_used=self.family_used(now))
                    g["false_streak"] = 0
            self._save_gov()

    def family_status(self, now: float) -> dict:
        g = self.gov
        return {"budget": self.cfg.family_locates_per_hour, "used_last_hour": self.family_used(now),
                "paused_until": g["until"] if g["until"] > now else None, "pause_level": g["level"],
                "reason": g["reason"] if g["until"] > now else "", "false_streak": g["false_streak"],
                "self": self.self_owner}

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
        """Call after new fixes arrive for a person. `seqs` maps device id -> oldest-first
        (ts, lat, lon, acc) list (a list of lists, or a single list, is accepted too). Each device is
        only compared with its own fixes, so an iPad at home and a phone in a pocket never look like
        a jump. Starts following when a device moves and remembers THAT device as the target; hands
        over if another of the person's devices is the one moving; stops when the moving device has
        come to rest. Returns True while following because of movement."""
        if isinstance(seqs, dict):
            items = [(k, q) for k, q in seqs.items() if q]
        else:
            if seqs and isinstance(seqs[0], tuple):
                seqs = [seqs]
            items = [(None, q) for q in seqs if q]
        if not self.follow_enabled(owner):
            return False
        window = self.cfg.follow_rest_minutes * 60
        with self.lock:
            following = self.follow_until.get(owner, 0) > now
            if following and now - self.follow_started.get(owner, now) > self.cfg.follow_max_hours * 3600:
                # runaway guard (e.g. noisy fixes): stop, and don't restart for an hour
                self.follow_until[owner] = 0
                self.cap_until[owner] = now + 3600
                db.log_locate_event("follow_stop", owner, now, reason="cap", minutes=round((now - self.follow_started.get(owner, now)) / 60, 1))
                return False
            if following:
                newest = max((q[-1][0] for _, q in items), default=now)
                movers = [(q[-1][0], k) for k, q in items if self.moved_recently(q, newest - window)]
                if movers:
                    target = max(movers)[1]
                    if target and target != self.follow_device.get(owner):
                        db.log_locate_event("follow_handover", owner, now, device=target, previous=self.follow_device.get(owner))
                        self.follow_device[owner] = target
                    self.follow_until[owner] = now + window  # expires on its own if fixes stop coming
                    return True
                tgt = self.follow_device.get(owner)
                tgt_seq = next((q for k, q in items if k == tgt), None)
                if (tgt_seq and self.at_rest(tgt_seq)) or (tgt_seq is None and any(self.at_rest(q) for _, q in items)):
                    self.follow_until[owner] = 0
                    db.log_locate_event("follow_stop", owner, now, reason="at_rest", device=tgt,
                                        minutes=round((now - self.follow_started.get(owner, now)) / 60, 1))
                    return False
                self.follow_until[owner] = now + window
                return True
            if now < self.cap_until.get(owner, 0):
                return False
            movers = [(q[-1][0], k) for k, q in items if self.moving(q)]
            if not movers:
                return False
            target = max(movers)[1]
            self.follow_device[owner] = target
            self.follow_started[owner] = now
            self.follows_started += 1
            self.follow_until[owner] = now + window
            db.log_locate_event("follow_start", owner, now, reason="moving", device=target)
            # the first fast locate is due straight away
            self.last_sent[owner] = min(self.last_sent.get(owner, now), now - self.cfg.moving_locate_minutes * 60)
            return True

    def locate_target(self, owner: str, now: float) -> str | None:
        """Device to locate for this person right now: the moving device while following because of
        movement; None means 'their main device' (normal schedule or manual follow)."""
        if self.following(owner, now) == "moving":
            return self.follow_device.get(owner)
        return None

    def start_manual(self, owner: str, minutes: float, now: float) -> None:
        with self.lock:
            if minutes <= 0:
                self.manual_until.pop(owner, None)
                self.follow_until[owner] = 0
                db.log_locate_event("follow_stop", owner, now, reason="manual_stop")
            else:
                self.manual_until[owner] = now + minutes * 60
                db.log_locate_event("follow_start", owner, now, reason="manual", minutes=minutes)
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
