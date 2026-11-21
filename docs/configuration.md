# Configuration

Settings come from three places, each overriding the one before:

1. **Defaults** in `famloc/config.py`.
2. **`data/config.toml`**, read at start. Restart after editing:
   `systemctl --user restart family-locator`.
3. **The app's Setup and Alerts tabs,** for the settings marked *UI* below. These are saved in the
   database and apply immediately, without a restart.

`config.example.toml` is a starting point. In the TOML file, keys can sit at the top level or
inside any `[section]`; sections are only for readability.

## Web

| Key | Default | Meaning |
|---|---|---|
| `host` | `0.0.0.0` | Address to listen on. |
| `port` | `8790` | Port to listen on. |
| `public_url` | blank | Fixed outside link for emails. Blank uses the gateway's Cloudflare quick tunnel. |
| `tunnel_metrics_url` | `http://127.0.0.1:20241/quicktunnel` | Where cloudflared reports its quick-tunnel address. |
| `proxy_prefix` | `/family/` | The path the radio gateway forwards to this app. Used to build outside links. |

## Tracking

| Key | Default | UI | Meaning |
|---|---|---|---|
| `poll_seconds` | 300 | yes | How often to read the positions Apple already has (60–3600 s). Costs nothing on the devices. |
| `locate_minutes` | 15 | yes | How often to ask every device for a fresh fix (0 = never, at least 5). Wakes family devices and uses some battery. |
| `with_family` | true | | Include Family Sharing members' devices. |
| `airtag_poll_minutes` | 15 | yes | How often to fetch AirTag history (5–1440 min). |

## Email and alerts

| Key | Default | UI | Meaning |
|---|---|---|---|
| `email_enabled` | true | | Master switch for sending email. |
| `email_address` | blank | | Gmail address to send from. |
| `email_app_password` | blank | | Gmail app password. |
| `email_recipient` | blank | | Where alerts go. Blank = `email_address`. Comma-separate several addresses. |
| `smtp_host`, `smtp_port` | `smtp.gmail.com`, 465 | | SMTP over SSL. |
| `low_battery_pct` | 15 | yes | Low-battery alert threshold (0 = off). |
| `stale_hours` | 12 | yes | Not-reporting alert after this many hours (0 = off). |
| `geofence_exit_margin_m` | 50 | yes | Extra distance beyond a place's radius before "left" fires. |
| `max_alert_accuracy_m` | 500 | yes | Fixes less accurate than this are ignored for place alerts. |
| `quiet_from_hour`, `quiet_to_hour` | -1, -1 | yes | No alert emails between these local hours (-1 = off). |
| `digest_hour` | 20 | yes | Local hour for the daily summary (-1 = off). |
| `person_dedupe_minutes` | 15 | | One email per person when several of their devices cross within this window. |

## Insights

| Key | Default | UI | Meaning |
|---|---|---|---|
| `stay_radius_m` | 150 | yes | A stay is time spent within this distance of one spot... |
| `stay_min_minutes` | 10 | yes | ...for at least this long. |

## Search and maps

| Key | Default | Meaning |
|---|---|---|
| `nominatim_url` | `https://nominatim.openstreetmap.org` | Geocoder for address search and "What's here?". Point it at your own Nominatim to keep searches local. |
| `thunderforest_key` | blank | Optional [Thunderforest](https://www.thunderforest.com/) API key (the free tier is enough). Adds the "Transport (Thunderforest)" base map. The key is sent to the browser to fetch tiles. |

## Environment

| Variable | Meaning |
|---|---|
| `FAMLOC_DATA` | Use a different data folder. The tests and demo use this so they never touch the real data. |

## Per-browser preferences

The base map, which layers are on, the time range, which people are expanded and the map position
are remembered in each browser. Clearing site data resets them; nothing else is affected.
