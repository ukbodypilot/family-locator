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
