---
name: ld2420-presence-daemon
description: "Setup, configure, and troubleshoot an LD2420 mmWave presence sensor daemon for Home Assistant. Covers: UART wiring on Raspberry Pi/DietPi, gate sensitivity tuning, MQTT discovery (12 entities), HDMI-CEC screen power control, HA automations for screen ON/OFF, minimum on-time anti-flicker logic, and env-var-based configuration. Use this skill when the user mentions LD2420, presence sensor, mmWave radar, presence daemon, radar-based screen control, or similar."
---

# LD2420 Presence Daemon Skill

This skill captures everything learned from setting up an LD2420 mmWave radar sensor with a Python daemon that controls a kitchen dashboard monitor. Use it to guide users through the full setup or troubleshoot existing installs.

## Architecture

```
LD2420 (UART) → presence_daemon.py → MQTT broker → HA entities (12x)
                                    → HDMI-CEC power (monitor ON/OFF)
                                    ← MQTT subscribe (HA override)
```

The daemon runs on a Raspberry Pi (DietPi), reads the LD2420 via UART at 256000 baud, and publishes to MQTT. It also controls the monitor via HDMI-CEC (`cec-client`).

## Key Concepts

### Gate Sensitivity Tuning

The LD2420 has 9 detection gates (0-8). Each gate has moving and still energy thresholds (0-255). Lower = more sensitive.

- **Gate 0** (~0-75cm): Set to **30 moving / 30 still** for ~50cm range
- **Gates 1+**: Set to **200/200** (minimal) or **255/255** (disabled)
- `MAX_GATE=1` means only gate 0 is active — all others disabled

The `valid_target` check requires:
- `moving_energy >= 20` OR `still_energy >= still_energy_threshold` (default 50)

So even with a sensitive gate, if baseline still_energy stays below 50, no false triggers.

### Env-var configuration

All config lives in `presence_config.env` — no code changes needed:
- `GATE0_MOVING`, `GATE0_STATIC` — gate 0 sensitivity (default: 30)
- `MAX_GATE` — highest active gate (default: 1)
- `STILL_ENERGY_THRESHOLD` — software still_energy filter (default: 50)
- `HDMI_POWER_CONTROL` — set `true` to enable CEC power control
- `CONFIRM_FRAMES`, `RELEASE_FRAMES` — debounce (default: 10 frames ≈ 0.2s each)
- `MQTT_BROKER`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS` — MQTT connection

### Screen Control Logic

The daemon has three layers of screen control, from fastest to slowest:

1. **Direct** — On presence transition, calls `_set_screen()` immediately (~2s)
2. **Minimum on-time** — 60s timer prevents flicker; brief dropouts are blocked
3. **MQTT override** — HA can still force ON/OFF (bypasses timer via `force=True`)

HDMI-CEC commands (`echo 'on 0' | cec-client -s -d 1` / `echo 'standby 0' | cec-client -s -d 1`) run via `os.system()`.

### MQTT Discovery

12 entities published under `homeassistant/` prefix:
- 1 binary_sensor (`kitchen_presence_ld2420`)
- 5 sensors (distance, moving_energy, still_energy, detection_state, status)
- 6 gate energy sensors (gates 0-2, moving + still)

Base topic: `home/dashboard/kitchen/presence`
Screen control: `home/dashboard/kitchen/screen/set` (subscribe)

### Systemd Service

Service file: `presence-daemon.service`
- Auto-starts on boot
- Restarts on failure (serial reconnect logic built in)
- Logs via `journalctl -u presence-daemon -f`

## Common Issues & Fixes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| False triggers at long range | Gate 0 too sensitive | Raise `GATE0_MOVING`/`GATE0_STATIC` (e.g., 30 → 50) |
| No detection up close | Gate 0 not sensitive enough | Lower gate 0 values (e.g., 30 → 15) |
| Screen flickers (ON/OFF rapidly) | Brief presence dropouts | Min on-time (60s default) should handle this |
| `CEC power ON (exit=0)` but monitor stays off | CEC not supported by monitor/TV | Check `cec-utils` installed, test manually: `echo 'on 0' \| cec-client -s -d 1` |
| Serial `Config enable: no reply` | Sensor not powered or wrong baud | Check 5V wiring, verify `/dev/serial0` exists, match baud 256000 |
| `IndentationError` at runtime | Bad edit/whitespace in daemon.py | Check for mixed tabs/spaces around edit area |

## Wiring Reference

| LD2420 | Raspberry Pi |
|--------|-------------|
| VIN | Pin 2 (5V) |
| GND | Pin 6 (GND) |
| OT1 (TX) | Pin 8 (GPIO14 / TXD) |
| RX | Pin 10 (GPIO15 / RXD) |

Enable UART: `enable_uart=1` + `dtoverlay=disable-bt` in `/boot/config.txt`.

## Files in the Repo

| File | Purpose |
|------|---------|
| `presence_daemon.py` | Main daemon (SerialReader, PresenceController, MQTT, CEC) |
| `presence_config.env.example` | Template for env config |
| `presence-daemon.service` | Systemd unit file |
| `ha_screen_automations.yaml` | Optional HA automations for screen ON/OFF |
| `PRESENCE_DAEMON_SETUP.md` | Step-by-step setup guide |

## Quick Test

```bash
python3 presence_daemon.py
# Walk toward sensor, watch for "target=True"
# Walk away, screen should turn OFF after ~2s (respecting 60s min on-time)
```
