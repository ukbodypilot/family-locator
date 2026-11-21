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
    public_url: str = ""  # used in alert emails; defaults to http://<lan-ip>:<port>

    # iCloud Find My polling
    poll_seconds: int = 300  # read Apple's cached positions this often
    locate_minutes: int = 15  # ask devices for a FRESH fix this often (0 = never; costs battery)
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
    "locate_minutes": ("Ask devices for a fresh fix every (0 = never)", 0, 1440, "min"),
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
        if k == "locate_minutes" and 0 < v < 5:
            v = 5  # don't hammer family devices' batteries
        setattr(cfg, k, v)
        clean[k] = v
    return clean
