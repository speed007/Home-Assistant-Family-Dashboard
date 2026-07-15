## 🏢 Dynamic Family Hub Dashboard With Home Assistant

Disclaimer: I am not a programmer! I do this as a hobby and because I like to break stuff! Enjoy!

A premium, ambient dark-mode home automation command center designed for multi-profile layouts. This ecosystem handles dual-frontend presentation layers seamlessly syncing via an asynchronous MQTT pipeline connected to Home Assistant integrations and a Telegram chatbot orchestration layer.

## Telegram Integration Notes
You will need to create a Bot in your Telegram account (many tutorials are out there). Put your `TELEGRAM_BOT_TOKEN` in the root `.env` file — copy `.env-example` to `.env` and fill in your details.
Once your Telegram Bot is up and running you can add it to your existing family group or better still create a new group for this dashboard and add members, also make sure you make the Bot admin to have access to your group messages where the Bot is added.

## Where to run?
I am running this on a Proxmox LXC instance but you can run on a Raspberry Pi from Zero 2W up to Pi 5!
You can also run on an old pc/laptop. The project really doesnt need much resources.
* For Kiosk LCD screen/monitor in Portrait mode, all it would need is a single webpage of `http://your-instance-ip:8080/kiosk` this can be run on Pi Zero 2W with dietPi OS and Cog Browser by following `PiZero2W_KioskSetupGuide.pdf`, which would launch at startup without any desktop with above URL. This setup uses about 350 MB of RAM and a few % of CPU! This is a 1080p portrait setup.
  
* You can access the non-kiosk dashboard at `http://your-instance-ip:8080/dashboard` from any PC, laptop, mobile phone or tablet.

## ⚡ Architecture Overview

* **🖥️ Kiosk Interface (`/kiosk`):** Hard-locked portrait dimension configuration tailored perfectly for a dedicated vertical 1080p monitor.
* **📱 Mobile Web Client (`/dashboard`):** Fluid, glassmorphic (`backdrop-filter`) auto-wrapping card array matching the kiosk aesthetic while adjusting flawlessly to handheld touch screens.
* **🤖 Telegram Bot Dispatcher:** A Python-driven SQLite backend layer acting as a remote management pipeline. Interacts with the family on the go to inject custom sticky notes, manipulate real-time grocery lists, and handle calendar adjustments.
* **📡 Async MQTT Service Broker:** The real-time messaging highway utilizing Mosquitto to stream state changes across layouts instantly.
* **🏡 Home Assistant Engine integration:** Pushes live local system data such as regional precise Adhan times (Fajr, Dhuhr, Asr, Maghrib, Isha) and localized weather conditions.

---

## 🛠️ Tech Stack & Dependencies

* **Frontend UI:** React.js, Vite, HTML5 Canvas/CSS Grid, Glassmorphic Styling Matrix
* **Brokerage Layer:** Eclipse Mosquitto (MQTT over WebSockets & standard TCP)
* **Proxy Routing:** Nginx
* **Orchestration Engine:** Docker Compose
* **Backend Automation:** Python 3, `python-telegram-bot`, `paho-mqtt`, SQLite3
* **Home Automation Hub:** Home Assistant (Jinja2 Templates & MQTT Publish actions)

---

🚀 Deployment Guide
1. Prerequisites
Ensure you have Docker and Docker Compose installed on your host server machine:
```
curl -fsSL [https://get.docker.com](https://get.docker.com) -o get-docker.sh
sudo sh get-docker.sh
```

2. Environment Configurations

Two `.env` files are needed — one for the backend, one for the frontend.

**Backend `.env` (root directory):**
Copy `.env-example` to `.env` in the project root and fill in your details:

```
MQTT_BROKER=your_mqtt_broker_ip
MQTT_PORT=1883
MQTT_USER=your_mqtt_username
MQTT_PASS=your_mqtt_password
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
HA_URL=http://your-ha-instance:8123
HA_TOKEN=your_long_lived_ha_token
TZ=Europe/London
```

**Frontend `.env` (`dashboard/` directory):**
Copy `dashboard/.env-example` to `dashboard/.env` and fill in your MQTT WebSocket details:

```
VITE_MQTT_BROKER_WS=ws://your-mqtt-ip:9001
VITE_MQTT_USER=your_mqtt_username
VITE_MQTT_PASS=your_mqtt_password
```

3. Build Frontend Assets & Start Containers

Build the React app (using pnpm or npm):

```
cd dashboard && pnpm install && pnpm build && cd ..
```

Then copy `meal_plan.json-example` to `meal_plan.json` and update with your weekly meal plan.

Finally, start everything:

```
docker compose up -d --build
```

## 📡 Integrations

Home Assistant YAML example files are provided in the `HomeAssistant/` folder. Copy them into your HA configuration and update entity IDs for your own devices.

## 🤖 Here is the complete list of triggers and keywords to use with your Telegram Bot.

Your bot relies on regular expressions (re.search and pattern matching) to parse your messages. It doesn't matter if you type them in UPPERCASE, lowercase, or Sentence Case—the backend handles them cleanly.

🛒 1. Smart Grocery & Shopping List
```
🛒 Smart Grocery & Shopping List: * Add triggers: Matches buy, need, get, shop, want, add, add to shopping list, add to shopping, add to grocery, add to groceries.

List prefixes: Matches lines starting with -, *, ▫️, or •.

Removals: Matches remove, delete, cancel, drop, bought (e.g. remove milk).

Bulk Clear: Matches clear shopping, clear shopping list, done shopping, etc.
```
📅 Family Schedule & Appointments:

```
Add triggers: Matches appt, appointment, book, schedule, event, calendar (e.g. schedule dentist on 12/07).

Removals: Matches remove appt 1, delete schedule 2, cancel appointment dentist, etc.

Bulk Clear: Matches clear appointments, clear calendar, clear schedule.
```

📋 Active Sticky Notes:

```
Add triggers: Matches note, sticky, remind, remember, memo (e.g. note lock back door).

Removals: Matches remove note 1, delete sticky fix tap, etc.

Bulk Clear: Matches clear notes, clear sticky, delete notes.
```

🍽️ Ad-Hoc Meal & Food Planning:

```
Day Overrides: Matches meal, dinner, food, menu, eat followed by today, tomorrow, monday, mon, tuesday, tue, wednesday, wed, thursday, thu, friday, fri, saturday, sat, sunday, sun (e.g. menu monday burgers or eat fri pasta).

Bulk Clear: Matches clear menu, clear meal plan, reset menu, delete menu.
```

🛠️ 5. General Utility & Diagnostics
```
/start – Greets you, builds baseline database structures if they're missing, and prints a helpful instructional layout map directly into your chat window.
```

## 🔐 Security & Safety Notice


All sensitive database entries (*.db), persistent system logs (logs/), localized runtime keys (.env), and security credentials directories (mosquitto/config/passwd) are explicitly managed by standard root boundaries and strictly filtered out via the workspace .gitignore array.
