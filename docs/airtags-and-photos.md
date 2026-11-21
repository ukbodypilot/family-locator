# AirTags and photo history

## AirTags

Apple doesn't show AirTags on iCloud.com, and their location reports are end-to-end encrypted.
Family Locator reads them through [FindMy.py](https://github.com/malmeloo/FindMy.py), which needs
each AirTag's keys. Each fetch returns several days of history, not just the latest position.

### 1. Get the key files (on a Mac)

On a Mac signed in to the Apple ID that owns the AirTags:

```bash
pip install findmy
python -m findmy decrypt --out-dir keys
```

This writes one JSON file per accessory into `keys/`. Recent macOS versions make the Find My cache
harder to decrypt, so check FindMy.py's README if this fails. The raw `.plist` files from the Find
My cache are also accepted.

### 2. Copy them to the server

Put the files in `data/airtags/`, for example with `scp keys/*.json gateway:~/Downloads/family-locator/data/airtags/`.
The Setup tab lists the key files it found.

### 3. Sign in

On the **Setup** tab under *AirTags*, enter your Apple ID and password and verify the code. This
sign-in is separate from the iCloud one. FindMy.py's saved session has the password removed before
it's written to `data/findmy_account.json`, so when Apple expires it you sign in again.

AirTags are fetched every 15 minutes (**AirTag fetch every** on the Setup tab). They appear under
**Items** in the People list and as grey tag markers on the map. They can be ticked in a place's
**Who this applies to** to get arrival and departure alerts.

## Photo history

Photos carry the location they were taken at. Importing them gives you a history going back years,
long before Family Locator was installed. Photo locations are kept as their own "devices" and
**never trigger alerts**.

### From iCloud Photos

Sign in to iCloud first, then choose **Import from iCloud Photos** on the Setup tab. The app reads
each photo's location from iCloud's metadata without downloading the photos. A large library takes
a while; progress is shown under the button, and a summary is added to Recent activity when it
finishes. Running it again only adds photos it hasn't seen.

This doesn't work with Advanced Data Protection turned on, because the metadata is then encrypted.

### From a folder

Type a folder path on this server into **Import folder**, for example an export or a Google
Takeout. JPEG, PNG, TIFF and DNG are read. HEIC needs the optional `pillow-heif` package
(`.venv/bin/pip install pillow-heif`). The time comes from the GPS timestamp if there is one,
otherwise from the photo's own date.

### Seeing it

Photo locations appear as small amber squares (the **Photo locations** layer), clustered when
zoomed out. They count in the heatmap. Choose **All** on the timeline to see everything.

### Removing an import

Each source can be deleted on its own with `DELETE /api/photos/icloud` or `DELETE /api/photos/folder`
(see [How it works](architecture.md#http-api)), or by hiding the "iCloud Photos" device.
