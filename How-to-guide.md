# How-To: New Features Setup & Usage Guide

This walks through getting the three new features actually working:
**send list**, **shopping done**, and **Alexa appointment announcements**.

If you haven't already deployed the base fixes from the first round, do
that first (see `FIXES_README.md`) — this guide assumes the bot is already
running and talking to MQTT/HA successfully.

---

## Part 1: "Send list" and "shopping done"

### What you need before this works

These two commands talk directly to Home Assistant's REST API, so the bot
needs a Home Assistant long-lived access token. If you haven't created one:

1. In Home Assistant, click your **profile** (bottom-left, your name/avatar)
2. Scroll down to **Security** tab
3. Under **Long-lived access tokens**, click **Create Token**
4. Name it something like `family-dashboard-bot`
5. **Copy the token immediately** — HA only shows it once

### Configure the bot

Open your `.env` file (the one in `~/family-dashboard/`, next to
`docker-compose.yml`) and fill in:

```
HA_URL=http://192.168.1.XXX:8123
HA_TOKEN=paste_your_long_lived_token_here
```

Replace `192.168.1.XXX` with your actual Home Assistant IP or hostname.

### Restart the bot

```bash
cd ~/family-dashboard
docker-compose restart telegram-bot
docker logs family-dashboard-telegram-bot -f
```

Watch the logs for a few seconds to confirm it starts cleanly (no
`KeyError` about missing `HA_URL`/`HA_TOKEN` — if you see that, the `.env`
didn't get picked up; double check there's no typo in the variable names).

### Try it

In your family Telegram group, send any of:

| You type | Bot does |
|---|---|
| `send list` | Posts current shopping list |
| `send shopping list` | Same |
| `show the list` | Same |
| `what's on the list` | Same |
| `shopping done` | Clears every item from HA's shopping list |
| `done shopping` | Same |
| `clear the list` | Same |

**Expected output for "send list":**
```
🛒 Shopping List:
• milk
• bread
✅ eggs
```
(✅ means already checked off in HA; • means still pending)

**Expected output for "shopping done":**
```
✅ Shopping list cleared! (3 items removed)
```

### If it doesn't work

```bash
# Check the bot can actually reach HA
docker logs family-dashboard-telegram-bot | grep -i "ha\|home assistant"

# Test the token works at all, from the Pi/server running the bot:
curl -H "Authorization: Bearer YOUR_TOKEN_HERE" \
     http://192.168.1.XXX:8123/api/states/shopping_list.shopping_list
```

That `curl` should return JSON with an `items` array. If you get `401
Unauthorized`, the token is wrong or expired. If you get `404`, the
`shopping_list:` integration probably isn't enabled in your HA
`configuration.yaml` (it should already be, from the original setup).

---

## Part 2: Alexa appointment & notes announcements

### What you need before this works

- **Alexa Media Player** integration installed via HACS and already
  connected to your Amazon account (you confirmed this is already set up)
- Know the `media_player.*` entity IDs of your Echo devices

### Find your Echo entity IDs

1. In Home Assistant, go to **Settings → Devices & Services → Entities**
2. Search for **"echo"** or **"alexa"**
3. Note each device's entity ID (e.g. `media_player.echo_dot_back_room`)

### Update the automation file (one-time setup)

Open `ha_automations.yaml` and check the `target.entity_id` lists in the
Alexa automations match your actual Echo device IDs. The file currently
targets all three Echo dots by default; add/remove devices as needed.

### Confirm the notify service works (do this before relying on it)

In Home Assistant: **Developer Tools → Actions** (or "Services" on older
versions). Search for `notify.alexa_media`. If it's not in the list, your
Alexa Media Player integration needs attention before the automations
below will do anything.

Run a quick manual test:
- Action: `notify.alexa_media`
- Target: your `media_player.*` entity (or a list of them)
- Data:
  ```yaml
  message: "Testing, testing"
  data:
    type: announce
  ```

Click **Perform Action**. Your Echo should chime and speak "Testing,
testing." If it doesn't, fix this before moving on.

### Install the automations

1. Open `ha_automations.yaml` and copy the three automation blocks:
   `alexa_appointment_day_before`, `alexa_appointment_hour_before`,
   and `alexa_note_announcement`
2. In Home Assistant: **Settings → Automations & Scenes → Automations**
3. Click the **three-dot menu (⋮)** top-right → **Edit in YAML**
4. Paste in each automation (or add them to your existing
   `automations.yaml` file directly if you manage config by file)
5. Save

### Test it for real

Calendar triggers in HA only re-check every ~15 minutes, so to test
without waiting a full day:

1. Add a test appointment via Telegram: `dentist tomorrow at 2pm`
2. Temporarily edit the automation's `offset` to something close to now,
   e.g. if it's currently 2:50pm and your appointment is at 3:00pm, set
   `offset: "-00:10:00"` to fire in ~10 minutes (do this for both
   day-before and hour-before automations separately)
3. Wait for the calendar to refresh and the automation to fire
4. **Revert the offset** back to `-24:00:00` / `-01:00:00` once confirmed working

### Expected behavior once live

- **24 hours before** an appointment: Echoes announce *"Reminder: you have
  'Dentist' tomorrow at 2:00 PM."*
- **1 hour before**: Echoes announce *"Reminder: 'Dentist' starts in about
  an hour."*
- **When a new note is added**: Echoes announce *"New note from [name]:
  [message]"*

### If it doesn't work

```bash
# Check Home Assistant's automation trace
# Settings → Automations & Scenes → click the automation → "..." → Traces

# Check the calendar entity actually has the event on it
# Developer Tools → States → search "calendar.telegram"
# Look at its attributes for upcoming events
```

Common issues:
- **Automation never fires**: the calendar entity ID in the trigger
  (`calendar.telegram`) doesn't match your actual calendar entity —
  check Developer Tools → States.
- **Fires but no sound**: an Echo entity ID is wrong, or the device is
  in Do Not Disturb mode.
- **Wrong time announced**: double-check the appointment's time was parsed
  correctly when it was added — check Developer Tools → States →
  `calendar.telegram` attributes for the actual stored time.
- **Notes not announced**: verify `HA_URL` and `HA_TOKEN` are set in the
  bot's .env so it can fire the `telegram_note_posted` event.

---

## Part 3: Displaying the dashboard on a kiosk screen (FullPageOS)

This covers getting `dashboard.jsx` actually up on a wall-mounted screen via
[FullPageOS](https://github.com/guysoft/FullPageOS) - a Raspberry Pi image
that just boots straight into a full-screen Chromium pointed at a URL you
configure. FullPageOS itself needs no special setup for this project beyond
pointing it at the right address - **don't point it at Home Assistant's own
`:8123` dashboard URL**; that's HA's native Lovelace UI, a separate thing
from the custom dashboard this project builds, and it won't show meal plan
data (that only exists in the bot's database/MQTT topic, not as an HA
entity).

### Build the React app

If you haven't already turned `dashboard.jsx` into a real app:

```bash
cd ~/family-dashboard
npx create-react-app dashboard
```

Copy `dashboard.jsx` in as `dashboard/src/App.js`, then create
`dashboard/.env` per the instructions in `FIXES_README.md` (the
`REACT_APP_MQTT_BROKER_WS` / `REACT_APP_MQTT_USER` / `REACT_APP_MQTT_PASS`
vars - point `REACT_APP_MQTT_BROKER_WS` at whichever host runs the
`mqtt` container from `docker-compose.yml`, on port 9001). Then:

```bash
cd dashboard
npm install
npm run build
```

This produces `dashboard/build/` - a folder of static HTML/JS/CSS.

### Serve it

`docker-compose.yml` now includes a `dashboard-web` service (nginx) that
serves `./dashboard/build` on port 8080. From the project root:

```bash
cd ~/family-dashboard
docker-compose up -d dashboard-web
```

Confirm it's up: `http://<host-ip>:8080` should show the dashboard (shopping
list will be empty until MQTT data starts flowing, which is expected).

### Point FullPageOS at it

On the FullPageOS SD card, the file to edit is `/boot/firmware/fullpageos.txt`
(older FullPageOS builds use `/boot/fullpageos.txt` - check whichever exists
on your card). Replace its contents with:

```
http://<host-ip>:8080
```

where `<host-ip>` is the IP of whichever machine is running the
`dashboard-web` container - if that's the same Pi running FullPageOS itself,
use `http://localhost:8080` instead. Reboot the FullPageOS Pi and it should
come up full-screen on the dashboard.

### If it doesn't work

- **Blank/white screen**: check `docker logs family-dashboard-web` and
  confirm `dashboard/build/index.html` actually exists - `npm run build`
  needs to have completed successfully first.
- **Dashboard loads but says "🔴 Offline" / nothing populates**: the
  browser's MQTT-over-websocket connection is failing - open the same URL
  in a normal desktop browser and check the console (F12) for the specific
  MQTT connection error; double check `dashboard/.env`'s
  `REACT_APP_MQTT_BROKER_WS` matches your actual broker's reachable
  address and port 9001, and that `mosquitto.conf` allows a websocket
  listener on that port.
- **FullPageOS still shows the old HA dashboard after editing
  `fullpageos.txt`**: confirm you edited the file on the boot partition
  while the SD card was in another computer (or via SSH into the right
  path on the Pi itself), then reboot - the file is only read at boot.

---

## Quick reference: all bot commands

| Say this in the group | What happens |
|---|---|
| `add milk` / `we need eggs` / `buy bread` | Adds to shopping list |
| `dentist Tuesday at 2pm` | Adds appointment |
| `pasta on Wednesday dinner` | Adds to meal plan |
| `send list` | Posts current shopping list |
| `shopping done` | Clears the shopping list |