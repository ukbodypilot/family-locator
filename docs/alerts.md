# Alerts and the daily summary

Alerts are emails. Every alert is also listed on the **Alerts** tab, whether or not it was emailed.

## What sends an email

| Alert | When |
|---|---|
| Arrived at a place | Someone's fix lands inside a saved place they weren't in before. |
| Left a place | Someone's fix is clearly outside the place: beyond the radius plus a 50 m margin, allowing for the fix's accuracy. |
| Low battery | A phone, iPad or Watch drops to 15% or less. Sent once; it re-arms after the battery climbs back above 25%. |
| Not reporting | A phone, iPad or Watch hasn't sent a fix for 12 hours. Sent once until it reports again. AirPods and Macs routinely go quiet for days, so they're not included. |
| iCloud sign-in needed | Apple stopped accepting the saved session. Tracking pauses until you sign in again on the Setup tab. |
| AirTag sign-in needed | The same for the separate AirTag sign-in. |
| Daily summary | Every day at 20:00, if email is set up. |

Every email carries two links that open the app on the right person:

- **Open (phone, anywhere):** through the radio gateway's Cloudflare tunnel. See
  [Phone access](phone-access.md).
- **Open (home Wi-Fi):** directly to the machine on your network.

Place alerts also include an OpenStreetMap link to the exact spot.

## How place alerts decide

- **First sighting is silent.** When a place is created or moved, each device's first fix only
  records whether it's inside. This stops a flood of "arrived at Home" emails the moment you
  draw Home.
- **There's a margin on the way out.** You're inside once a fix lands within the radius. You've
  left only once you're beyond the radius plus the margin. Fixes that wobble on the boundary
  don't send alternating arrive and leave emails.
- **Poor fixes are ignored.** Fixes worse than 500 m accuracy don't count.
- **One email per person.** When someone's phone and watch cross a boundary within 15 minutes of
  each other, only the first sends an email. Both are still listed on the Alerts tab.
- **Late fixes don't count.** A fix older than the last decision (for example AirTag history
  arriving late) can't change who's where now.

## Who a place applies to

On the place form, under **Who this applies to**:

- **Nobody ticked:** the place alerts for everyone.
- **Some people ticked:** only their devices trigger it, for example "tell me when Sam leaves
  School".
- **AirTags** can be ticked too, so an AirTag on a bag or bike can alert like a person.

Each place also has its own **arrives** and **leaves** switches.

## Quiet hours

Set **Quiet hours start** and **Quiet hours end** on the Alerts tab, in local hours from 0 to 23.
For example 22 and 7 gives no alert emails from 22:00 to 07:00. Events are still logged and show
"not emailed (quiet hours)". iCloud sign-in emails ignore quiet hours. Set either value to -1 to
turn quiet hours off.

## Daily summary

Sent once a day at **Daily summary email at** (default 20, or -1 to turn it off), only when email
is set up. For each person it lists:

- where they are now, or when their last fix was;
- km travelled and number of trips in the last 24 hours;
- each arrival and departure at saved places;
- a link that opens the app on them.

**Preview daily summary** on the Alerts tab shows it without sending. **Email summary now** sends
it straight away.

## Changing the thresholds

All of these are on the Alerts tab and take effect immediately:

| Setting | Default |
|---|---|
| Low battery alert below | 15% (0 turns it off) |
| Stale device alert after | 12 h (0 turns it off) |
| Quiet hours | off |
| Daily summary email at | 20 |
| Leave-alert margin beyond radius | 50 m |
| Ignore fixes worse than | 500 m |

The one-email-per-person window (15 minutes) is `person_dedupe_minutes` in `data/config.toml`.
