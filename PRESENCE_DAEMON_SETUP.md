# LD2420 Presence Daemon — Setup Guide

## Overview
Python daemon that reads an **LD2420 mmWave radar sensor** via UART, detects presence, and controls a monitor via **HDMI-CEC**. It publishes 12 MQTT entities via Home Assistant discovery and subscribes to `home/dashboard/kitchen/screen/set` for optional HA override.

## 1. Wiring

| LD2420 | Raspberry Pi |
|--------|-------------|
| VIN | Pin 2 (5V) |
| GND | Pin 6 (GND) |
| OT1 (TX) | Pin 8 (GPIO14 / TXD) |
| RX | Pin 10 (GPIO15 / RXD) |

Use **5V** — LD2420 has an onboard regulator.

## 2. Enable UART on DietPi/Raspberry Pi

```bash
echo "enable_uart=1" | sudo tee -a /boot/config.txt
echo "dtoverlay=disable-bt" | sudo tee -a /boot/config.txt
sudo systemctl disable hciuart
sudo reboot
```

After reboot, verify `/dev/serial0` exists.

## 3. Install Python deps

```bash
pip install pyserial paho-mqtt
```

## 4. Configure

Copy and edit the config file:

```bash
cp presence_config.env.example presence_config.env
nano presence_config.env
```

Required settings:
```
MQTT_BROKER=192.168.x.x
MQTT_PORT=1883
MQTT_USER=mqtt_user
MQTT_PASS=mqtt_pass
```

Optional, for HDMI-CEC power control:
```
HDMI_POWER_CONTROL=true
```

Range tuning (default ~50cm):
```
GATE0_MOVING=30
GATE0_STATIC=30
MAX_GATE=1
```

If using `MAX_GATE=1`, gate 0 is the only active detection zone (0–75cm). All other gates are disabled (sensitivity 255).

## 5. Test

```bash
python3 presence_daemon.py
```

Walk toward the sensor — logs show `target=True dist=30 screen=on`. Walk away — screen turns OFF after ~2s release delay (60s minimum on-time prevents flicker).

## 6. Auto-start on boot

```bash
sudo cp presence-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable presence-daemon
sudo systemctl start presence-daemon
```

Check logs:
```bash
sudo journalctl -u presence-daemon -n 50 -f
```

## 7. Home Assistant automations (optional)

The daemon publishes MQTT discovery under `homeassistant/` prefix — entities appear automatically in HA. To automate screen control from HA:

```yaml
alias: Kitchen Screen ON
trigger:
  - platform: state
    entity_id: binary_sensor.kitchen_presence_ld2420
    to: "on"
action:
  - service: mqtt.publish
    data:
      topic: home/dashboard/kitchen/screen/set
      payload: "ON"
```

The daemon's MQTT listener (`force=True`) bypasses the 60s min on-time for HA commands.

## 8. MQTT topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `home/dashboard/kitchen/presence` | Daemon publishes | JSON payload with presence, distance, energies |
| `home/dashboard/kitchen/screen/set` | Daemon subscribes | HA override — payload `ON` or `OFF` |
| `homeassistant/{component}/{id}/config` | Daemon publishes | MQTT Discovery (12 entities) |

## 9. Behaviour summary

- **~2s** presence detection → screen ON via CEC
- **~2s** release delay → screen OFF (with 60s minimum on-time)
- MQTT commands from HA override the timer
- Gate sensitivity tuned via env vars (no code changes)
