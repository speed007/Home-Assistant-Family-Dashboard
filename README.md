# 🏢 Dynamic Family Hub Dashboard & Telemetry Stream

A premium, ambient dark-mode home automation command center designed for multi-profile layouts. This ecosystem handles dual-frontend presentation layers seamlessly syncing via an asynchronous MQTT pipeline connected to Home Assistant integrations and a Telegram chatbot orchestration layer.

---

## ⚡ Architecture Overview

The system is fully containerized and broken down into isolated service meshes routed natively via Nginx:

* **🖥️ Kiosk Interface (`/kiosk`):** Hard-locked portrait dimension configuration tailored perfectly for a dedicated vertical 15.6" monitor hallway assembly.
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

## 📦 Directory Structure

```text
family-dashboard/
├── dashboard/               # React + Vite Frontend Client App
│   ├── src/                 # Application Entry and Components
│   └── views/               # Multi-profile View Layouts (Kiosk / Mobile UI)
├── mosquitto/               # MQTT Broker Workspace Data & Access Controls
├── logs/                    # Engine runtime log volumes
├── docker-compose.yml       # Complete Container Multi-Service Spec
├── nginx.conf               # Custom Reverse Proxy Configuration Layer
├── telegram_bot.py          # Python Chatbot Dispatcher Core
├── db.py                    # Local SQLite3 Persistent Storage Handler
├── ha_automations.yaml      # Jinja2 Home Assistant Automation Payloads
└── requirements.txt         # Python Engine Dependencies
```

🚀 Deployment Guide
1. Prerequisites
Ensure you have Docker and Docker Compose installed on your host server machine:
```
curl -fsSL [https://get.docker.com](https://get.docker.com) -o get-docker.sh
sudo sh get-docker.sh
```

2. Environment Configurations
Create a local .env configuration template in the root directory (Note: This file is intentionally hidden from Git tracking for protection):
```
TELEGRAM_BOT_TOKEN=your_secure_api_token_here
MQTT_BROKER=your_mqtt_broker_ip
MQTT_PORT=1883
MQTT_USER=your_mqtt_username
MQTT_PASS=your_mqtt_password
```
# Optional — enables forwarding sticky notes to Home Assistant as events
```
HA_URL=http://your-ha-instance:8123
HA_TOKEN=your_long_lived_ha_token
```
3. Spin Up the Container Infrastructure
Compile your deployment profile production assets and start the system containers detached:

# Build frontend assets via Vite
```
cd dashboard && npm install && npm run build && cd ..
```
# Launch core runtime structures
```
docker compose up -d --build
```
📡 Integrations

Home Assistant Prayer Times Sync Payload
The layout receives flat JSON data payloads over the home/dashboard/prayer_times topic. Use the following dynamic automation configuration inside Home Assistant to broadcast automatically at midnight and system boots:
```
alias: "Dashboard: Sync Prayer Times"
mode: single
triggers:
  - at: "00:05:00"
    trigger: time
  - event: start
    trigger: homeassistant
actions:
  - action: mqtt.publish
    data:
      topic: home/dashboard/prayer_times
      retain: true
      payload: '{"Fajr":"{{ as_timestamp(states("sensor.salah_fajr"), default=0) | timestamp_custom("%I:%M %p", true, "12:00 AM") }}","Dhuhr":"{{ as_timestamp(states("sensor.salah_dhuhr"), default=0) | timestamp_custom("%I:%M %p", true, "12:00 AM") }}","Asr":"{{ as_timestamp(states("sensor.salah_asr"), default=0) | timestamp_custom("%I:%M %p", true, "12:00 AM") }}","Maghrib":"{{ as_timestamp(states("sensor.salah_maghrib"), default=0) | timestamp_custom("%I:%M %p", true, "12:00 AM") }}","Isha":"{{ as_timestamp(states("sensor.salah_isha"), default=0) | timestamp_custom("%I:%M %p", true, "12:00 AM") }}"}'
```
Dashboard: Sync Weather Data Automation

```
alias: "Dashboard: Sync Weather Data"
description: ""
triggers:
  - entity_id: weather.forecast_home
    trigger: state
actions:
  - data:
      topic: home/dashboard/weather
      retain: true
      payload: |-
        {
          "temperature": {{ state_attr('weather.forecast_home', 'temperature') | default('—', true) }},
          "condition": "{{ states('weather.forecast_home') | default('Clear', true) }}",
          "feels_like": {{ state_attr('weather.forecast_home', 'apparent_temperature') | default('—', true) }}
        }
    action: mqtt.publish
```
Dashboard: Sync Multiple Calendars to MQTT
replace with your own calendars' data!

```
alias: "Dashboard: Sync Multiple Calendars to MQTT"
description: >-
  Pulls upcoming appointments from both AK and wife calendars, merges them, and
  publishes to the React Dashboard.
triggers:
  - trigger: time_pattern
    minutes: /15
  - trigger: homeassistant
    event: start
actions:
  - action: calendar.get_events
    target:
      entity_id: calendar.husband
    data:
      duration:
        days: 7
    response_variable: husband_agenda
  - action: calendar.get_events
    target:
      entity_id: calendar.wife
    data:
      duration:
        days: 7
    response_variable: wife_agenda
  - action: mqtt.publish
    data:
      topic: home/dashboard/calendar_events
      retain: true
      payload: >-
        {% set ns = namespace(combined=[]) %}

        {# Loop through AK Calendar events and tag them #} {% if
        ak_agenda['calendar.husband'] is defined %}
          {% for event in ak_agenda['calendar.husband'].events %}
            {% set start_dt = as_datetime(event.start) %}
            {% set date_str = start_dt.strftime('%Y-%m-%d') %}
            {% if event.start is string and event.start | length == 10 %}
              {% set time_str = "All Day" %}
            {% else %}
              {% set time_str = start_dt.strftime('%H:%M') %}
            {% endif %}
            {% set ns.combined = ns.combined + [{"title": "[Husband] " ~ event.summary, "date": date_str, "time": time_str}] %}
          {% endfor %}
        {% endif %}

        {# Loop through wife Calendar events and tag them #} {% if
        wife_agenda['calendar.wife'] is defined %}
          {% for event in wife_agenda['calendar.wife'].events %}
            {% set start_dt = as_datetime(event.start) %}
            {% set date_str = start_dt.strftime('%Y-%m-%d') %}
            {% if event.start is string and event.start | length == 10 %}
              {% set time_str = "All Day" %}
            {% else %}
              {% set time_str = start_dt.strftime('%H:%M') %}
            {% endif %}
            {% set ns.combined = ns.combined + [{"title": "[Wife] " ~ event.summary, "date": date_str, "time": time_str}] %}
          {% endfor %}
        {% endif %}

        {# Sort the merged list chronologically by their event dates #} {% set
        sorted_events = ns.combined | sort(attribute='date') %}

        {{ {"events": sorted_events} | to_json }}
mode: restart

```
Dashboard: Sync Presence Status

```
alias: "Dashboard: Sync Presence Status"
description: ""
triggers:
  - entity_id:
      - person.father
      - person.mother
      - person.kids
    trigger: state
actions:
  - data:
      topic: home/dashboard/presence
      retain: true
      payload: |-
        {
          "Father": "{{ states('person.father') | title }}",
          "Mother": "{{ states('person.mother') | title }}",
          "Kids": "{{ states('person.kids') | title }}"
        }
    action: mqtt.publish
```

🔐 Security & Safety Notice
All sensitive database entries (*.db), persistent system logs (logs/), localized runtime keys (.env), and security credentials directories (mosquitto/config/passwd) are explicitly managed by standard root boundaries and strictly filtered out via the workspace .gitignore array.
