# Getting started

This guide takes you from a fresh checkout to a running app with your family on the map.

## 1. Install

```bash
git clone https://github.com/ukbodypilot/family-locator ~/Downloads/family-locator
cd ~/Downloads/family-locator
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Everything the app keeps lives in `data/`. That folder isn't in git, and the app sets it to
owner-only permissions.

## 2. Configure email (optional, but you'll want alerts)

```bash
mkdir -p data && cp config.example.toml data/config.toml
```

Edit `data/config.toml` and fill in the `[email]` section:

```toml
[email]
email_address = "you@gmail.com"
email_app_password = "abcd efgh ijkl mnop"   # a Gmail app password, not your normal password
email_recipient = ""                          # blank = email_address; comma-separate several
```

Create the app password at <https://myaccount.google.com/apppasswords>. On the gateway machine
these are the same values as the radio gateway's `EMAIL_*` settings.

Every other setting can be changed later in the app. See [Configuration](configuration.md).

## 3. Start it

```bash
.venv/bin/python -m famloc.main
```

Open `http://<this machine's address>:8790` in a browser.

## 4. Choose the page password

The first visit asks you to choose a password for the page, 8 characters or more. Until you do,
anyone on your network could set it, so do this straight away.

## 5. Sign in to iCloud

On the **Setup** tab, under *iCloud Find My*:

1. Enter your Apple ID and password, then choose **Sign in to iCloud**.
2. Approve the sign-in on one of your Apple devices and type the 6-digit code into **Verify code**.
   If no prompt appears, choose **Text me a code instead**.

Your devices and your family's devices appear on the **People** tab within a minute.

Your Apple ID password is never saved. Only Apple's session cookies are kept, in
`data/icloud_session/`, so a restart resumes without a password. When Apple eventually expires
the session, you get an email asking you to sign in again.

Each family member needs location sharing turned on in their Family Sharing settings, or their
devices show "No location yet".

## 6. Add your places

Places make the app much more useful: the timeline lanes, status lines and alerts are all built
around them.

1. Open the **Places** tab and choose **Add a place**.
2. Click the map where the place is. You can also right-click anywhere and choose
   **Create place here**, or search for an address and choose **Make this a place**.
3. Name it, set the radius (150 m suits a house, 200–300 m a school or office), and choose
   **Save place**.

Start with Home. See [Alerts](alerts.md) for who gets alerted and when.

## 7. Run it as a service

On the gateway machine the app runs as a systemd user service. Lingering is on, so it starts at
boot without anyone logging in.

```bash
ln -sf ~/Downloads/family-locator/deploy/family-locator.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now family-locator
```

Useful commands:

```bash
systemctl --user status family-locator
systemctl --user restart family-locator     # after editing data/config.toml or updating the code
journalctl --user-unit family-locator -f    # live log
```

## 8. Use it on your phone

To reach the app from outside the house, see [Phone access](phone-access.md).

## Updating

```bash
cd ~/Downloads/family-locator
git pull
.venv/bin/pip install -r requirements.txt
systemctl --user restart family-locator
```

The database is upgraded automatically on start. Browsers pick up the new version on their next
page load.
