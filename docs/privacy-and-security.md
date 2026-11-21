# Privacy and security

Family Locator holds a detailed record of where your family has been. This page explains what it
keeps, where, what leaves the machine, and how it's protected.

## Tell your family

Everyone in the Family Sharing group can already see each other in Find My. This app goes further:
it keeps a **history**, and sends **emails** about arrivals and departures. Let them know.

## What's stored, and where

Everything lives in `data/` inside the app folder. The folder is owner-only (0700) and every file
in it is 0600. None of it is in git.

| File | Contents |
|---|---|
| `famloc.db` (+ `-wal`, `-shm`) | SQLite: devices, every saved fix, places, alert events, settings, sessions. |
| `backups/famloc-YYYY-MM-DD.db` | A daily copy of the database; the last 14 are kept. |
| `config.toml` | Settings, including the Gmail app password. |
| `icloud_session/` | Apple's session and trust cookies. Enough to read Find My until Apple expires them, so treat it like a password. |
| `findmy_account.json` | FindMy.py's AirTag session, with the password removed. |
| `airtags/` | AirTag key files. Anyone with these can locate the AirTags. |
| `last_fmip.json` | The latest raw Find My response, kept for troubleshooting. |

Writes are fully synced to disk before they count as saved, so a power cut can't corrupt the
history.

### Passwords

- **Apple ID password:** never written to disk. It's typed into the Setup tab and held only in
  memory while signing in. The keyring is disabled, so pyicloud can't save it there either.
- **The page password:** stored as a salted PBKDF2-SHA256 hash (300,000 rounds). Sessions are
  random tokens, kept for 30 days, in an HttpOnly, SameSite=Strict cookie.
- **Gmail app password:** in `config.toml`. It can only send email, and can be revoked at
  <https://myaccount.google.com/apppasswords>.

## What leaves the machine

| To | What | When |
|---|---|---|
| Apple (iCloud) | Your Apple ID session | Every poll, every 5 minutes by default |
| Gmail | Alert and summary emails | When alerts fire, and once a day |
| OpenStreetMap Nominatim | The text you type into search, or the point you click for "What's here?" | Only when you do it. Device positions are never sent. |
| Map tile servers (OSM, Esri, OpenTopoMap, CyclOSM) | Which map squares your browser shows | When you view the map. These requests come from your browser, not the server. |
| Cloudflare | Page traffic, when used through the tunnel | When you use the outside link |

Nothing else is sent: no analytics, no telemetry, no third-party scripts. Fonts and map code are
bundled with the app.

## Who can get in

- **On the home network:** the page password.
- **From outside:** only through the radio gateway's tunnel, which needs the gateway login and
  then the page password. See [Phone access](phone-access.md).
- **Login throttling:** after 5 wrong passwords from one address, logins from it are refused for
  5 minutes. Every attempt is logged with the address, as `[WebAccess]` lines:
  `journalctl --user-unit family-locator | grep WebAccess`.
- **Changing the password:** Setup tab, **Web password**.
- **No password yet:** a page with no password set accepts the first one anyone chooses. Set it
  straight after installing.

## Deleting data

- **One device:** hide it from its **⋯** menu. To remove its history completely, delete it from
  the database (`DELETE FROM points WHERE device_id = '...'`).
- **Photo imports:** see [AirTags and photo history](airtags-and-photos.md#removing-an-import).
- **Everything:** stop the service and delete `data/`. Delete `data/backups/` too, or the history
  survives there.
- **Stop tracking:** **Sign out of iCloud** on the Setup tab. This deletes the saved Apple
  session; nothing more is collected.
