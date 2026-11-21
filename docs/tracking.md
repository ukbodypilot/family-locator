# Tracking and following

Find My only knows where a device was the last time it reported. Family Locator gets new locations
in two ways:

- **Reading.** Every 5 minutes (`poll_seconds`) it reads the positions Apple already holds. This is
  a request to Apple only; the phones aren't involved at all. It only shows something new when a
  phone has reported in by itself.
- **Fresh locations.** It asks Apple to get a new fix from a person's phone right now, exactly like
  opening Find My and selecting them. Apple sends the phone a push message over the connection it
  already keeps open, and the phone takes one quick fix and answers within about 5 to 30 seconds.

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
