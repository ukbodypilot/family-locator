# Troubleshooting

Start with the log:

```bash
systemctl --user status family-locator
journalctl --user-unit family-locator -n 100
```

On this machine the system journal is kept in memory, so use `--user-unit`.
`journalctl --user -u ...` can show nothing.

## Blank page, or "Family Locator didn't start"

If the app can't start, the page shows the error after 8 seconds and sends it to the log:

```bash
journalctl --user-unit family-locator | grep ClientError
```

Reloading usually fixes it. If it keeps happening, clearing this site's data in the browser resets
the saved map layers and time range; nothing on the server is affected.

## A control does nothing after an update

The page loads the app's script and stylesheet with a version number, so a browser can't run an
old cached script with a new page. If something still behaves oddly after an update, reload the
page; on a desktop, Ctrl+Shift+R forces a full reload.

## "No location yet" or "without a location" for some devices

Apple only has locations for devices that are switched on, online and have location sharing
turned on. Old Macs and switched-off iPads stay without one. For a family member with no location
at all, check that location sharing is on in their Family Sharing settings. **Show last known
location** in a device's **⋯** menu shows the last fix this app saved, even for offline devices.

## Locations update slowly

- **Cached positions:** every 5 minutes the app reads the positions Apple already holds. These
  only change when a device reports in.
- **Fresh fixes:** every 15 minutes it asks each person's phone for a fresh fix, every 2 minutes
  while they're moving. Each person can have their own interval on the Setup tab. See
  [Tracking and following](tracking.md).
- **One person, right now:** use **Locate now** on a device, or **Locate everyone now** on the
  Setup tab.
- **No answer:** a device that's asleep or out of signal won't respond.

## "iCloud re-login needed" email

Apple expires saved sessions now and then. Open the Setup tab and sign in again with the code.
Nothing is collected in the meantime.

## The outside link doesn't open

- **"Cross-site request refused."** The gateway is missing the `/family` entries in
  `_NAVIGABLE_EXTRA`, or wasn't restarted after they were added.
- **The page doesn't load at all.** The tunnel address changed when cloudflared restarted. Use the
  link on the Setup tab or in a newer email.
- **"Family Locator is not running".** The gateway is up but the app isn't:
  `systemctl --user restart family-locator`.
- **Signed out on every visit.** Check the browser allows cookies for the trycloudflare address.

## Emails don't arrive

- **Setup tab, Email:** shows "set up" or "not set up", plus counts of sent and failed emails.
  **Send a test email** shows the actual error.
- **Error mentions "Username and Password not accepted":** the Gmail app password is wrong or was
  revoked. Make a new one and restart.
- **Event says "not emailed (quiet hours)":** check the quiet hours on the Alerts tab.
- **Daily summary never comes:** it only sends when email is set up, at the hour set (default 20),
  and while the app is running at that hour.

## Too many, or missing, place alerts

- **Repeat arrive and leave emails:** increase **Leave-alert margin beyond radius**, or make the
  place bigger.
- **Arrivals missed:** a small place can be crossed between fixes. Make the radius bigger, or
  fresh fixes more frequent.
- **Nothing for one person:** check **Who this applies to** on the place.
- **Shown on the Alerts tab but not emailed:** a second device of the same person crossed within
  15 minutes; only the first one emails.

## Map tiles look blurry when zoomed right in

You're past that map's real detail; Light grey and Dark grey stop at zoom 16. Switch to Streets,
Dark streets, Satellite or Hybrid, which go to zoom 19.

## Heatmap or tracks look empty

They show the range on the timeline. With only a few hours of history there's little to draw;
choose **7d** or **All**.

## Testing changes without touching real data

```bash
FAMLOC_DATA=~/famloc-scratch/data .venv/bin/python tools/seed_demo.py    # made-up family, 14 days
printf 'port = 8791\nhost = "127.0.0.1"\nemail_enabled = false\n' > ~/famloc-scratch/data/config.toml
FAMLOC_DATA=~/famloc-scratch/data .venv/bin/python -m famloc.main        # http://127.0.0.1:8791
FAMLOC_DATA=$(mktemp -d) .venv/bin/python tests/test_core.py             # unit tests
```

The demo script refuses to run against the real `data/` folder.
