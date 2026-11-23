# Tracking and following

Find My only knows where a device was the last time it reported. Family Locator gets new locations
in two ways:

- **Reading.** Every minute (`poll_seconds`) it reads the positions Apple already holds. This is
  a request to Apple only; the phones aren't involved at all. It only shows something new when a
  phone has reported in by itself.
- **Fresh locations.** It asks Apple to get a new fix from a person's phone right now, exactly like
  opening Find My and selecting them. Apple sends the phone a push message over the connection it
  already keeps open, and the phone takes one quick fix and answers within about 5 to 30 seconds.

## Find My sessions, and why family positions need them

The app talks to Apple the way the iCloud.com Find My page does. Each session's settings, sent by
Apple, describe a live viewer: check back every 2 to 60 seconds, and a session lifespan of 15
minutes. What we measured on 2026-11-21:

- **Your own devices:** fresh-location requests work. Apple replies `isLocating: true` and switches
  the session to live (check back every 2 s).
- **Family members' devices:** Apple ignores fresh-location requests (`isLocating: false`, check back
  every 30 s). Their new positions reach us when a **new session** starts. Fresh fixes for all three
  arrived within a minute of each new session. Within a session they go stale, apart from occasional
  reports from the phones themselves.

So the app reads every 60 seconds (`poll_seconds`), starts a fresh session every 5 minutes
(`session_renew_minutes`), every 2 minutes while any family member is moving
(`session_renew_moving_minutes`, never under 1), every 10 minutes once the whole family has been
at rest for 30 minutes (`session_renew_quiet_minutes`, `quiet_after_minutes`), typically overnight, and a watchdog starts one early if family fixes stop for 10 minutes while
their devices are online. **For family members, the session interval is what sets freshness.**
Following them faster can't help. Family fresh-location requests are kept to a probe (2 an hour) in
case Apple's behaviour changes.

Measured on 2026-11-21 (family fix age, median / 90th percentile / worst, in minutes):

| Session every | Median | p90 | Worst | Sessions/h |
|---|---|---|---|---|
| 10 min | 3.4 | 9.8 | 19.9 | 6 |
| 5 min | 2.0 | 5.2 | 13.2 | 12 |
| 3 min | 0.9 | 2.0 | 4.0 | 19 |
| 2 min | 0.5 | 1.3 | 2.4 | 27 |

Apple sent no errors, login challenges or session changes at any interval. The defaults (5 normal,
2 while moving, 10 when quiet) keep 2-minute sessions to the times someone is travelling.

After a locate request the app reads every 15 s until Apple's own locate deadline
(`maxLocatingTime`, 90 s) plus 10 s. Your own phone's answers arrive in 4-8 s while it moves.

## Per-person intervals

Each person has their own **Fresh location** interval on the Setup tab:

| Choice | Meaning |
|---|---|
| Default | The global **Fresh fix for each person every** setting, 15 minutes unless changed. |
| 1 min to 1 hour | This person only. 1 minute is the shortest. |
| Never | No fresh locations for this person. Reading still picks up whatever their phone reports by itself. |

The request goes to the person's main phone: their iPhone with the most recent fixes, or else their
Watch or iPad. People are staggered 20 seconds apart, so the family isn't all woken at once.

## Follow while moving

When someone sets off, their locations get more frequent until they arrive:

Each device is only ever compared with its own earlier fixes, so an iPad left at home and a
phone in a pocket never look like a jump.

Following always targets **the device that's actually moving**. If someone leaves their phone at
home and goes out with a cellular Watch, the Watch is followed and the phone isn't, and following
ends when the moving device comes to rest.

1. **Starts:** a device's new fix lands more than 250 m from its previous one (`movement_threshold_m`), and
   further than the two fixes' combined accuracy, so GPS wobble doesn't count. That happens on their
   next fix after they leave: within their normal interval, or sooner if their phone reports by itself.
2. **While moving:** a fresh location every 2 minutes (`moving_locate_minutes`, as often as every
   30 seconds). The person shows "Moving, following, every 2 min".
3. **Stops:** every fix in the last 10 minutes (`follow_rest_minutes`) is close to where they are
   now. They're back on their normal interval.
4. **Safety cap:** one follow never runs longer than 4 hours (`follow_max_hours`). If it hits the
   cap, for example because of noisy fixes, it won't restart for an hour. If fixes stop arriving
   altogether, following ends after the rest time anyway.

**Follow while moving** can be set per person (Default, On, Off). The global default is
**Follow people while they move** on the Setup tab.

### Example

A phone on the default 15 minutes, parked, then driving for 20 minutes, then parked again:

```
0:00  fresh location (15-min rhythm)
0:15  fresh location
0:30  fresh location        ← sets off just after
0:36  moving detected: fresh location every 2 min  (0:36, 0:38 … 0:58)
0:50  arrives and parks
0:59  no movement for ~10 min: back to every 15 min
1:13  fresh location
```

## The family budget

Apple appears to limit how often one account can actively locate **other family members'**
devices. When it does, its replies still look successful, but show `isLocating: false`, and no new
fix arrives. Locating your own devices carries on as normal. So requests are governed for the whole
family, not person by person:

- **One budget:** at most 20 fresh-location requests an hour across all family members together
  (**Family budget** on the Setup tab).
- **Spacing:** any two requests, for anyone, are at least 20 seconds apart.
- **Priority when the budget is tight:** manual follows first, then people who are moving, then
  normal schedules. Within each group, whoever has waited longest goes first.
- **Family-wide pause:** after 3 family requests in a row come back with `isLocating: false`, all
  family requests pause for 15 minutes. Then one probe request goes out. If Apple acts on it,
  everything resumes. If not, the pause doubles, up to 2 hours.
- **Your own devices are exempt:** the signed-in Apple ID's devices aren't counted against the
  budget and aren't paused.
- **Restarts don't reset it:** request history and any pause are saved in the database.
- **Per-person backoff too:** a person whose phone doesn't produce a new fix after 3 requests in a
  row has their interval doubled, up to once an hour, until it answers again. For your own devices,
a request Apple acted on (`isLocating: true`) that returns the stored fix means the device hasn't
moved. It doesn't count: only requests Apple ignores back off.

The Setup tab shows how much of the budget has been used this hour, or the pause and its reason.
After a restart, the first requests wait 2 minutes and are spread 30 seconds apart.

To see what's happening:

```bash
journalctl --user-unit family-locator | grep "locate "      # Apple's reply to each request
curl -s localhost:8790/metrics | grep -E "family|answered|backoff"
```

A proxy or a different IP address wouldn't help: requests are signed in as your Apple ID, so any
limit follows the account.

## Follow now

**Follow for 30 minutes** in a person's ⋯ menu starts following straight away, whether or not
they're moving. It's handy when you know someone is about to set off. **Stop following** ends any
follow.

## What it costs the phones

Very little. Reading costs the phones nothing. A fresh location is one push message over the
connection the phone already keeps open to Apple, plus one quick location fix, often from Wi-Fi and
cell towers rather than full GPS. That's small next to what a phone already does all day on LTE or
5G with background services. Apple's own Find My updates a followed friend every few seconds while
the app is open. A request every 15 minutes, or every 2 minutes while travelling, won't be
noticeable.

## Is there an Apple limit?

Apple doesn't publish one for the Find My web service. The app's floors are its own choices: 60
seconds for reading, 1 minute per person for fresh locations, and 30 seconds while following. They
keep the app from looking abusive to an unofficial API. Requesting far more often, for many people
at once, is what's most likely to get the saved session expired early.
