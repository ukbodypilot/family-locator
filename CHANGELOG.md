# Changelog

All notable changes to Family Locator. The version is the git tag.

## [1.11.2] -- 2026-11-23

Docs for parents of a new driver.

- **Docs: keeping an eye on a new driver.** A README section and a user-guide how-to for parents of
  a teen who has started driving: arrival emails, speed-coloured trips, follow while moving,
  replaying a drive, and what the speeds can and can't tell you.

## [1.11.1] -- 2026-11-23

New dark-mode screenshots.

- **New screenshots, in dark mode,** showing speed-coloured tracks, playback following someone at
  75 mph, the heatmap, places and the phone layout. The demo seeder (`tools/seed_demo.py`) adds a
  drive, a bike ride and walks on real road geometry so every speed band appears.

## [1.11.0] -- 2026-11-22

Usable on a phone, the map follows who you click, and tracks are coloured by speed.

- **Usable on a phone.** On a narrow screen the timeline folds down to one row (range buttons and
  play), and a chevron opens the speed and trail sliders and the lanes; the choice is remembered.
  The map gets the rest of the screen: the +/- buttons and scale bar are hidden (pinch to zoom),
  the speed legend fits on one line, the attribution is smaller, and the page height follows
  iOS Safari's moving toolbars (`100dvh`). Reloading no longer slides the details panel up over
  the map because the address still named the last tab (`#people`).
- **The map follows who you clicked.** Clicking a person or device, on the map or in the list,
  centres the map on them and keeps it centred as they move: on live updates and during
  playback. A "Centred on (name)" chip shows while it's on; drag the map or press its × to stop.
  Zooming keeps it: while it's on, the scroll wheel, double-click and pinch zoom around the
  person instead of the pointer, so they stay in the middle. Clicking a group of people centres
  once without following.
- **Tracks coloured by speed.** Each stretch between two fixes is coloured by its speed, rainbow
  order: purple walking (under 4 mph), blue 4-10, green 10-25, yellow 25-50, orange 50-75, red
  75+. Legend at the bottom left, the band on hover, and a halo under the line so it reads on any
  map. A stretch over a gap of more than 15 minutes, or touching a fix less accurate than 200 m,
  is grey and dashed ("no speed"), so a long hop isn't passed off as a slow drive. On by default;
  turn it off under **Tracks coloured by speed** in the layers menu to get the per-person track
  colours back. Shades tuned so neighbouring bands stay distinguishable, including for
  colour-blind viewers.
- **Speeds are in mph everywhere:** the map label, person card, people list and Insights trips.

## [1.10.0] -- 2026-11-22

Speed everywhere you look at a moving person.

- **Speed on the map marker.** A moving person's name label reads e.g. "Sam 120 km/h · 74 mph".
  Merged markers (people together) don't show it; open the card for each person.
- **Speed in the people list.** A moving person's status line shows their average speed too,
  e.g. "On the move · 62 km/h (39 mph)". Hover it for how many fixes it's based on. It follows
  the timeline during playback, worked out from the saved fixes; nothing extra is stored.

## [1.9.0] -- 2026-11-22

See how fast someone is going.

- **Average speed on the person card.** While someone is moving, their map card shows a Speed row,
  e.g. "62 km/h (39 mph), avg of 3 fixes over 4 min". It's the path length over the last 3 fixes
  from the device that's with them, or up to 6 fixes when the last 3 span less than 3 minutes.
  It shows only when the newest fix is under 15 minutes old and they've moved more than 250 m
  (or more than the fixes' accuracy), so GPS jitter at rest never shows as a speed. Fixes less accurate than 200 m are
  skipped. Works in timeline playback too.

## [1.8.0] -- 2026-11-22

Survives reboots and expired Apple sessions without you having to sign in again.

- **Remember password (optional).** A tick-box on the iCloud sign-in (or **Remember password…**
  while signed in) keeps the Apple ID password in `data/icloud_secret`, owner-only and outside the
  database, so the app signs back in by itself when Apple ends the session. If Apple wants a 2FA
  code, you get an email and type it on the Setup tab. A saved password Apple rejects is deleted.
  **Forget saved password** and **Sign out** delete it.
- **Fix: a network failure at startup no longer stops tracking.** Starting before the network was
  up (after a reboot) was read as "Apple expired the session": tracking stopped and never retried.
  Connection errors, timeouts and Apple 5xx now retry from 30 s, doubling to 10 min. A successful
  resume also re-arms the re-login email, which the false alarm had used up.

## [1.7.0] -- 2026-11-22

A one-command installer, and a map and timeline that are easier to focus.

- **Installer.** `./install.sh` checks for Python 3.11+, creates `.venv` and installs the
  packages, sets up an owner-only `data/` with a starter `config.toml`, and installs and starts a
  systemd user service pointing at wherever you cloned it. Safe to re-run. `--update` pulls and
  restarts, `--no-service` skips the service, `--uninstall` removes it and keeps your data. Getting
  started and the README now use it; the manual steps are still documented.
- **Show or hide people and devices on the map.** An eye button on each person and device, **Show
  only (name)** in a person's menu, and a **Show all** bar while anything is hidden. Hiding a
  device takes it out of the person's position, e.g. an iPad left at home. Saved per browser;
  alerts and tracking are unchanged.
- **Timeline sliders.** Playback speed and trail length are continuous log-scale sliders instead
  of drop-downs: speed 1 s (real time) to 1 hour per second, default 10 min; trail 15 min to 7
  days, or whole track at the far right. Remembered per browser, and shown on phones too.
- **Roomier timeline.** Lanes are 28 px tall (20 px on phones, were 16 and 12), with more space
  around them and larger names and time labels.
- **Clicking a person's marker** (or a group of people) centres the map on them, keeping the zoom,
  and opens their card there.
- Screenshots updated.

## [1.6.0] -- 2026-11-21

Family positions about a minute old instead of several, and your own phone answers in seconds.

- **Session timing from a day of measurement.** A fresh Find My session every 5 minutes (was 10),
  every 2 while any family member is moving, and every 10 in **quiet mode**, once the whole family
  has been at rest for 30 minutes. Measured family fix age: 2.0 min median at 5-min sessions,
  0.5 at 2-min, against 3.4 at 10-min. Apple pushed back at no interval (table in
  `docs/tracking.md`). New settings: `session_renew_moving_minutes`, `session_renew_quiet_minutes`,
  `quiet_after_minutes`.
- **Faster answers.** After a locate request the app reads fast until Apple's own locate deadline
  (90 s + 10 s, was 50 s), so a late answer no longer waits a whole cycle. Your own phone now
  answers in 4-8 s while moving (was about 2 min).
- **No backoff for your own phone at rest.** A request Apple acted on that returns the stored fix
  means "hasn't moved", not "no answer". Only requests Apple ignores slow the interval down.
- **Answer detection:** a fix from before a request isn't counted as its answer. Session fixes
  were being miscounted.
- **Page refresh** every 30 s while visible, and immediately on returning to the tab (was 60 s,
  and stale after backgrounding).
- **`/metrics`:** `famloc_session_mode` (schedule, moving or quiet).

## [1.5.0] -- 2026-11-21

Fresh family positions, and the map follows the device that's actually with each person.

- **Find My session renewal.** Family members' positions only refresh when a new Find My session
  starts. Apple ignores fresh-location requests for other people's devices (`isLocating: false`)
  and lets the session go stale. That caused the simultaneous "blackouts" for family in two
  countries while your own devices kept updating. The app now starts a fresh session every 10
  minutes (`session_renew_minutes`; Apple's lifespan is 15) and reads every 60 seconds, the longest
  gap Apple's own web client allows. A watchdog starts a session early if family fixes stop for 10
  minutes while their devices are online.
- **New defaults from measurement:** `poll_seconds` 60 (was 300), `session_renew_minutes` 10,
  `family_locates_per_hour` 2 (a probe only, since family requests are ignored).
- **Following targets the moving device.** Fast locates go to the device whose own fixes show
  movement, with hand-over if another device takes over. A device left behind is never located
  fast, and following ends when the moving device comes to rest.
- **The map uses the device that's with the person.** When a person's devices disagree by more
  than 300 m, the one that moved most recently is used, e.g. the Watch that left home rather than
  the phone that stayed. The phone is used when they agree. Marker, track, timeline lane and status
  line all use the same device, so tracks always join the marker. Fixes Luke's new position showing
  with no line to his earlier path.
- **Timeline:** clicking a lane centres the map on that person at that moment.
- **Measurement tools:**
  - a durable event log (`locate_events`, 30 days) of every request, Apple's reply, answers versus
    self-reported fixes, pauses, blocks, follows, session renewals and settings changes;
  - `tools/locate_report.py`;
  - `tools/tune.py`, for live setting changes with a reason log;
  - `tools/email_report.py`, with a `famloc-report` systemd timer for periodic report emails.
- **Settings apply live.** Saved settings are re-read every minute, so no restart is needed. The
  governor's limits (pause trigger, pause cap, spacing, per-person backoff) are now settings.

## [1.4.0] -- 2026-11-21

- **Family-wide request governor.** The evidence suggests Apple limits active locates of other
  family members' devices per account, not per device: Luke, never followed, stalled at the same
  moment as Hugh and Tomoko. So requests are now governed for the whole family:
  - one hourly budget for all family members (default 20);
  - at least 20 seconds between any two requests;
  - priority to manual follows, then moving people, then normal schedules;
  - a family-wide pause after 3 replies with `isLocating: false` (15 minutes, then a probe,
    doubling up to 2 hours).
  The account holder's own devices are exempt. Request history and pauses are saved, so restarts
  can't reset them.
- **"Locate everyone now"** counts against the family budget.
- **Setup tab** shows the family budget used this hour, or the pause and its reason. **`/metrics`**
  gains `famloc_family_*` figures.

## [1.3.2] -- 2026-11-21

- **Backs off when phones don't answer.** Each fresh-location request is checked for a new fix
  within 3 minutes. After 3 unanswered requests in a row, that person's interval doubles, up to
  once an hour, and resets as soon as they answer. This responds to Apple quietly limiting active
  locates of other family members' devices: replies stay successful but show `isLocating: false`,
  and no new fix follows.
- **No request burst after a restart.** The first requests wait 2 minutes and are spread 30
  seconds apart.
- **Diagnostics.** Apple's reply to every request is logged (`locate <person>: http=…
  isLocating=…`), `/metrics` has per-person answered, unanswered and backoff figures, and
  `/api/icloud/locate-log` lists the last 40 replies.

## [1.3.1] -- 2026-11-21

- **Fix: blank page when the heatmap had been left on.** The saved heatmap layer was re-added
  while the map was still hidden and 0 pixels wide. leaflet-heat then threw on a zero-size canvas
  and the app never started. Saved layers are now restored once the map is visible, and the heatmap
  skips drawing on a zero-size map.
- **Fix:** restoring saved layers no longer redraws the map before the device lists exist.
- **A blank page now explains itself.** Browser script errors are sent to the server log
  (`[ClientError]` lines, rate-limited). If the app hasn't started after 8 seconds, the page shows
  the error and a Reload button instead of staying blank.

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
