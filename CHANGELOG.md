# Changelog

All notable changes to Family Locator. The version is the git tag.

## [1.3.0] -- 2026-11-21

- **Faster tracking allowed.** A person's fresh-location interval can now be as short as 1 minute
  (was 2), and following can run as often as every 30 seconds (was 1 minute). The menus gain a
  1-minute option, and intervals under a minute show as seconds, e.g. "every 30 s".
- **More accurate wording on cost.** The app and docs now say it plainly: reading Apple's stored
  positions doesn't involve the phones at all. A fresh location is one push message over the
  connection the phone already holds, plus one quick fix, the same as selecting someone in Find My.
  The "uses battery" warnings were overstated and are gone. See
  [What it costs the phones](docs/tracking.md#what-it-costs-the-phones).

## [1.2.0] -- 2026-11-21

- **Per-person tracking.** Each person has their own fresh-location interval (Default, 2 min to 1
  hour, or Never) on the Setup tab. Requests go to that person's main phone, staggered so the
  family isn't woken at once.
- **Follow while moving.** When someone's fixes show they've set off, more than 250 m and beyond
  the fixes' combined accuracy, their phone is located every 2 minutes. It stops once they've
  stayed put for 10 minutes, with a 4-hour safety cap. On or off per person. A pulsing badge shows
  who's being followed.
- **Follow now.** "Follow for 30 minutes" and "Stop following" in a person's menu.
- **No more stalls.** Fresh locations no longer block the poller: requests return immediately and
  answers are collected on 10-second reads.
- **Settings fix.** On/off settings show as On and Off menus, and the settings are grouped in
  order.
- **One device at a time.** Movement is judged per device, so an iPad at home and a phone in a
  pocket never look like a jump.
- **`/metrics`** gains fresh-location and follow counters (people as `p0`, `p1`, …, never names).
- **Docs:** a new [Tracking and following](docs/tracking.md) guide.

## [1.1.0] -- 2026-11-21

- **Public transport base map** (ÖPNVKarte, from memomaps.de): bus, tram, metro and rail routes
  with line numbers and stops. No key needed.
- **Railways overlay** (OpenRailwayMap): rail, metro and tram lines on top of any base map,
  including satellite.
- **Optional Thunderforest Transport** base map, shown when `thunderforest_key` is set in
  `data/config.toml`.
- No longer hard-codes the home router's address when working out the home-network link.

## [1.0.0] -- 2026-11-21

First full release. Everything below, plus complete documentation in `docs/`.

- **Documentation.** A new README, plus guides for getting started, using the app, alerts, phone
  access, AirTags and photos, configuration, privacy and security, troubleshooting and how it
  works, with screenshots from demo data.
- **Cleaner activity list.** The Alerts tab shows each event's sentence with a **Show on map**
  link. The link lines meant for email are no longer shown raw in the app.
- **Better demo data.** `tools/seed_demo.py` creates three named places and replays two days
  through the real alert logic, so every screen has realistic content.

## [0.4.0] -- 2026-11-21

- **Phone access from anywhere.** The app works when served under `/family/` behind the radio
  gateway, which reaches the internet over its Cloudflare tunnel. Links are relative, the login
  cookie covers every path, and edit and delete requests are sent as POST with
  `X-HTTP-Method-Override`, because the gateway only proxies GET and POST. The real client address
  comes from `X-Forwarded-For`, trusted only from this machine, so failed-login throttling applies
  per visitor.
- **Links in every email.** Alerts, the daily summary and the iCloud re-login email carry an
  "anywhere" link through the tunnel and a home Wi-Fi link, both opening the app on the right
  person (`#focus=`). The tunnel address is read from cloudflared on each use, because it changes
  whenever cloudflared restarts.
- **Setup tab** shows the current phone link with a Copy button.
- **Gmail** set up using the gateway's settings.

## [0.3.1] -- 2026-11-21

- **Display names for people,** for when Apple's family data abbreviates a name. Used in the app,
  alert emails and the daily summary.
- **No more stale cached scripts.** The page asks for its script and stylesheet with a version
  number, so a browser can't pair a new page with an old cached script. That pairing had stopped
  the heatmap from turning on.
- **Better marker merging.** Markers merge whenever they would overlap on screen at the current
  zoom.
- **Brighter heatmap** for sparse Find My data.

## [0.3.0] -- 2026-11-21

A redesign around people instead of devices, following a design review.

- **People first.** One marker per person, from their freshest phone, watch or iPad fix. People at
  the same place share one marker. Status lines read "At *School* since 08:12", and devices fold
  away with a menu: Locate now, Show last known location, Insights and battery history, Rename,
  Hide.
- **Timeline lanes.** One row per person, coloured by place, moving or elsewhere, with night
  shading, hover details and a legend.
- **Live or past is always clear.** A "Live" banner, or "Viewing <time>" with **Back to now**. The
  automatic refresh never moves you while you're looking at the past.
- **Insights:** a **Name this place** link on frequent unnamed spots, a battery chart, and
  distance bars.
- **Alerts:** limit a place to certain people, one email per person when their phone and watch
  cross together, quiet hours, and a daily summary email with preview and send-now.
- **New look:** survey-map styling with Barlow Semi Condensed and Source Serif 4 italic place
  names (bundled), and an ink, paper, contour and water colour scheme. On a phone the panel is a
  bottom sheet. Keyboard and screen-reader support for tabs, visible focus, and the pulse animation
  stops when reduced motion is requested.
- **Fix:** the map now re-measures itself when the timeline changes height. Before this, tiles
  only filled a strip.

## [0.2.1] -- 2026-11-21

- **Zoom to level 22 on every base map,** enlarging the last real tiles. The default dark map had
  stopped at 16.
- **New sharp dark map,** "Dark streets (OSM)".

## [0.2.0] -- 2026-11-21

- **Settings in the app.** Polling and alert settings can be changed from the app.
- **Locate now** for a single device.
- **Tidier device list.** Devices without a location fold away.
- **Durable history:** fully synced writes, owner-only files, and a daily backup with 14 kept.
- **Fewer pointless alerts.** Not-reporting alerts only cover phones, iPads and Watches.

## [0.1.0] -- 2026-11-21

- **First version:**
  - iCloud Find My polling, including Family Sharing members, without storing the Apple ID
    password.
  - AirTags through FindMy.py, and past locations from photos.
  - Place alerts with a margin against repeat alerts, plus low-battery, not-reporting and
    re-login emails.
  - A Leaflet map with search, a timeline with playback, stays and trips, and a page password
    with login throttling.
