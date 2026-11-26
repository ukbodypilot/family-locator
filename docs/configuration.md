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
| `apps` | empty | Optional links for an "Apps" menu in the top bar, e.g. `apps = [{ name = "Tesla", url = "http://192.0.2.10:9877/" }]`. Empty = no menu. Hidden when the page is shown inside another app's frame. |
| `public_url` | blank | Fixed outside link for emails. Blank uses the gateway's Cloudflare quick tunnel. |
| `tunnel_metrics_url` | `http://127.0.0.1:20241/quicktunnel` | Where cloudflared reports its quick-tunnel address. |
| `proxy_prefix` | `/family/` | The path the radio gateway forwards to this app. Used to build outside links. |

## Tracking

| Key | Default | UI | Meaning |
|---|---|---|---|
| `poll_seconds` | 60 | yes | How often to read the positions Apple already has (60–3600 s). Costs nothing on the devices; 60 s is the longest gap Apple's own web client allows. |
| `locate_minutes` | 15 | yes | Default fresh-location interval per person (0 = never, at least 1). Each person can override it. |
| `follow_moving` | true | yes | Follow people while they move (each person can override it). |
| `moving_locate_minutes` | 2 | yes | Fresh-location interval while following (0.5–15 min; 0.5 = every 30 seconds). |
| `follow_rest_minutes` | 10 | yes | Stop following after no movement for this long. |
| `follow_max_hours` | 4 | yes | Longest single follow. |
| `movement_threshold_m` | 250 | yes | How far apart two fixes must be to count as moving (also beyond their combined accuracy). |
| `family_locates_per_hour` | 2 | yes | Budget for fresh-location requests to all family members together, per rolling hour. Your own devices don't count. |
| `family_pause_minutes` | 15 | yes | First family-wide pause when Apple stops acting on requests; doubles on each failed probe, up to 2 hours. |
| `family_throttle_after` | 3 | yes | Family replies in a row with `isLocating: false` before a family pause. |
| `family_pause_cap_minutes` | 120 | yes | Longest family pause. |
| `locate_spacing_s` | 20 | yes | Minimum gap between any two locate requests. |
| `unanswered_backoff_after` | 3 | yes | Unanswered requests in a row before a person's interval is doubled. |
| `session_renew_minutes` | 5 | yes | Start a fresh Find My session this often (0 = never). Family members' positions refresh at each new session; Apple's session lifespan is 15 minutes. |
| `session_renew_moving_minutes` | 2 | yes | Session interval while any family member is moving (floor 1). Each new session makes Apple locate every family device, so this is how family members get followed. |
| `session_renew_quiet_minutes` | 10 | yes | Session interval once every family member has been at rest for `quiet_after_minutes` (quiet mode). |
| `quiet_after_minutes` | 30 | yes | How long everyone must be at rest before quiet mode. |
| `with_family` | true | | Include Family Sharing members' devices. |
| `airtag_poll_minutes` | 15 | yes | How often to fetch AirTag history (5–1440 min). |

Per-person intervals and follow switches are set on the Setup tab and stored in the database. See [Tracking and following](tracking.md).

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
| `rough_accuracy_m` | 50 | yes | A fix less accurate than this is "rough". It can't trigger an arrival or departure by itself: it needs a good fix that agrees, or rough fixes that keep agreeing for 10 minutes, and a fix that contradicts it cancels it. Rough out-and-back jumps, and an unconfirmed rough newest fix, don't start or keep "follow while moving". |
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
