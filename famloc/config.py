"""Configuration: defaults overlaid by data/config.toml (gitignored)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FAMLOC_DATA", ROOT / "data"))
CONFIG_PATH = DATA_DIR / "config.toml"


@dataclass
class Config:
    # Web
    host: str = "0.0.0.0"
    port: int = 8790
    public_url: str = ""  # fixed public link for emails; blank = discover the gateway's Cloudflare tunnel
    tunnel_metrics_url: str = "http://127.0.0.1:20241/quicktunnel"  # cloudflared reports its quick-tunnel host here
    proxy_prefix: str = "/family/"  # path the radio-gateway proxies to this app

    # iCloud Find My polling
    poll_seconds: int = 60   # read Apple's stored positions this often (Apple's web client checks back at least every 60 s)
    locate_minutes: int = 15  # default per-person fresh-location interval (0 = never)
    follow_moving: bool = True        # locate a moving person more often until they stop
    moving_locate_minutes: float = 2.0
    follow_rest_minutes: float = 10.0  # "stopped" = no movement for this long
    follow_max_hours: float = 4.0      # safety cap on one follow
    movement_threshold_m: float = 250.0
    family_locates_per_hour: int = 2      # Apple ignores locate requests for family members' devices; keep a probe only
    family_pause_minutes: float = 15.0     # first family-wide pause when Apple stops acting on requests
    family_throttle_after: int = 3         # isLocating=false replies in a row before a family pause
    family_pause_cap_minutes: float = 120.0
    locate_spacing_s: float = 20.0         # minimum gap between any two locate requests
    unanswered_backoff_after: int = 3      # per person: unanswered requests in a row before slowing them
    session_renew_minutes: float = 10.0    # start a fresh Find My web session this often (Apple's sessionLifespan is 15 min); 0 = never
    session_renew_moving_minutes: float = 3.0  # ...this often while any family member is moving (floor 2)
    with_family: bool = True

    # AirTags (FindMy.py)
    airtag_poll_minutes: int = 15

    # Alerts
    email_enabled: bool = True
    email_address: str = ""
    email_app_password: str = ""
    email_recipient: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    low_battery_pct: int = 15
    stale_hours: float = 12.0  # alert when a device has not reported for this long (0 = off)
    geofence_exit_margin_m: float = 50.0  # hysteresis so a fix on the boundary doesn't flap
    max_alert_accuracy_m: float = 500.0  # ignore fixes worse than this for geofence decisions

    quiet_from_hour: int = -1  # no alert EMAILS from this hour... (-1 = no quiet hours)
    quiet_to_hour: int = -1    # ...until this hour (local). Events are still logged.
    digest_hour: int = 20      # daily summary email at this local hour (-1 = off)
    person_dedupe_minutes: float = 15.0  # phone + watch crossing together -> one alert

    # Analysis
    stay_radius_m: float = 150.0
    stay_min_minutes: float = 10.0

    # Geocoding (search box). Only typed queries / explicit "what's here" are sent.
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    thunderforest_key: str = ""  # optional; enables the "Transport (Thunderforest)" base map

    extra: dict = field(default_factory=dict)


def load() -> Config:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)
    cfg = Config()
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            raw = tomllib.load(f)
        flat: dict = {}
        for k, v in raw.items():
            if isinstance(v, dict):  # allow [web] / [email] style sections
                flat.update(v)
            else:
                flat[k] = v
        for k, v in flat.items():
            if hasattr(cfg, k) and k != "extra":
                setattr(cfg, k, type(getattr(cfg, k))(v))
            else:
                cfg.extra[k] = v
    if not cfg.email_recipient:
        cfg.email_recipient = cfg.email_address
    return cfg


# Settings editable from the web UI: name -> (label, min, max, unit). Stored as overrides in the
# DB (settings key "cfg_overrides") and applied on top of config.toml at startup and on save.
EDITABLE = {
    "poll_seconds": ("Read cached positions every", 60, 3600, "s"),
    "locate_minutes": ("Fresh location for each person every (0 = never)", 0, 1440, "min"),
    "follow_moving": ("Follow people while they move (1 = on, 0 = off)", 0, 1, ""),
    "moving_locate_minutes": ("While moving, fresh location every (0.5 = 30 s)", 0.5, 15, "min"),
    "follow_rest_minutes": ("Stop following after no movement for", 3, 60, "min"),
    "follow_max_hours": ("Never follow one trip longer than", 0.5, 24, "h"),
    "movement_threshold_m": ("Counts as moving beyond", 100, 2000, "m"),
    "family_locates_per_hour": ("Family budget: fresh locations per hour, all members together", 2, 120, ""),
    "family_pause_minutes": ("Pause family requests when Apple stops acting on them, for", 5, 120, "min"),
    "family_throttle_after": ("Pause after this many ignored family requests in a row", 1, 10, ""),
    "family_pause_cap_minutes": ("Longest family pause", 15, 480, "min"),
    "locate_spacing_s": ("Minimum gap between any two requests", 5, 300, "s"),
    "unanswered_backoff_after": ("Slow a person down after this many unanswered requests", 1, 20, ""),
    "session_renew_minutes": ("Renew the Find My session every (0 = never)", 0, 60, "min"),
    "session_renew_moving_minutes": ("...while a family member is moving, every", 2, 15, "min"),
    "airtag_poll_minutes": ("AirTag fetch every", 5, 1440, "min"),
    "low_battery_pct": ("Low battery alert below (0 = off)", 0, 100, "%"),
    "stale_hours": ("Stale device alert after (0 = off)", 0, 720, "h"),
    "quiet_from_hour": ("Quiet hours start, no alert emails (-1 = off)", -1, 23, "h"),
    "quiet_to_hour": ("Quiet hours end", -1, 23, "h"),
    "digest_hour": ("Daily summary email at (-1 = off)", -1, 23, "h"),
    "geofence_exit_margin_m": ("Leave-alert margin beyond radius", 0, 1000, "m"),
    "max_alert_accuracy_m": ("Ignore fixes worse than", 20, 5000, "m"),
    "stay_radius_m": ("Stay radius", 25, 2000, "m"),
    "stay_min_minutes": ("Stay minimum duration", 1, 600, "min"),
}


def apply_overrides(cfg: Config, overrides: dict) -> dict:
    """Validate + apply; returns the cleaned overrides."""
    clean = {}
    for k, v in (overrides or {}).items():
        if k not in EDITABLE:
            continue
        _, lo, hi, _ = EDITABLE[k]
        typ = type(getattr(cfg, k))
        v = typ(max(lo, min(hi, float(v))))
        if k == "locate_minutes" and 0 < v < 1:
            v = 1  # floor: one request a minute per person
        setattr(cfg, k, v)
        clean[k] = v
    return clean
