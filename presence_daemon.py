#!/usr/bin/env python3
"""Presence daemon for LD2420 mmWave radar sensor via UART on Pi Zero 2W.

Connects LD2420 → Pi UART (GPIO14/15), publishes to MQTT,
controls HDMI power via vcgencmd with configurable off-delay.

Wiring:
    LD2420 VIN  → Pi Pin 2 (5V)
    LD2420 GND  → Pi Pin 6 (GND)
    LD2420 OT1  → Pi Pin 8 (GPIO14/TXD)
    LD2420 RX   → Pi Pin 10 (GPIO15/RXD)
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time

import serial
import paho.mqtt.client as mqtt

logger = logging.getLogger("presence_daemon")


class SerialReader:
    """Read and parse LD2420 frames from serial port at 115200 baud.

    Frame format:
        [0xAA, 0xAA] [cmd:1] [len:1] [data...] [checksum:1]
    Data frame (cmd=0x01) payload:
        [has_target:1] [distance_low:1] [distance_high:1] [...]
    Checksum: (sum of cmd + len + data + checksum) & 0xFF == 0
    """

    HEADER = b"\xaa\xaa"
    FRAME_RATE_LIMIT = 1.0 / 50

    def __init__(self, port, baud=115200, timeout=0.1):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._buf = bytearray()
        self._last_read = 0.0
        self.ser = None
        self._connect()

    def _connect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
        )

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def read_frame(self):
        """Return (has_target: bool, distance: int) or None."""
        now = time.time()
        if now - self._last_read < self.FRAME_RATE_LIMIT:
            return None
        self._last_read = now

        try:
            chunk = self.ser.read(256)
        except serial.SerialException:
            return None
        if not chunk:
            return None

        self._buf.extend(chunk)

        while len(self._buf) >= 4:
            idx = self._buf.find(self.HEADER)
            if idx == -1:
                leftover = 1 if self._buf[-1:] == b"\xaa" else 0
                self._buf = self._buf[-leftover:] if leftover else bytearray()
                return None
            if idx > 0:
                del self._buf[:idx]

            data_len = int(self._buf[3])
            total = 4 + data_len + 1

            if len(self._buf) < total:
                return None

            frame = bytes(self._buf[:total])
            del self._buf[:total]

            if (sum(frame[2:]) & 0xFF) != 0:
                continue

            cmd = frame[2]
            if cmd == 0x01 and data_len >= 3:
                return (bool(frame[4]), frame[5] | (frame[6] << 8))

        return None


class PresenceController:
    """Manages presence state, HDMI power, and MQTT publishing."""

    def __init__(self, mqtt_broker, mqtt_port, mqtt_user, mqtt_pass,
                 screen_timeout=60, distance_threshold=50):
        self.screen_timeout = screen_timeout
        self.distance_threshold = distance_threshold
        self.has_target = False
        self.distance = 0
        self.last_known_distance = 0
        self.screen_on = True
        self._off_timer = None
        self._running = True

        self._mqtt = mqtt.Client()
        if mqtt_user:
            self._mqtt.username_pw_set(mqtt_user, mqtt_pass)
        self._mqtt.on_connect = self._on_mqtt_connect
        self._mqtt.on_disconnect = self._on_mqtt_disconnect

        try:
            self._mqtt.connect(mqtt_broker, mqtt_port, 60)
            self._mqtt.loop_start()
            logger.info(f"MQTT connecting to {mqtt_broker}:{mqtt_port}")
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")

    def update(self, has_target, distance):
        if has_target:
            self.has_target = True
            if distance > 0:
                self.last_known_distance = distance
            self.distance = distance
            self._cancel_timer()

            near = (
                self.distance_threshold == 0
                or self.last_known_distance <= self.distance_threshold
            )
            if near and not self.screen_on:
                self._turn_screen_on()

            logger.info(
                "target=%s dist=%d last_known=%d screen=%s",
                has_target, distance, self.last_known_distance,
                "on" if self.screen_on else "off",
            )
        else:
            if not self.has_target:
                return
            self.has_target = False
            self.distance = 0
            logger.info("Presence lost — starting %ds timer", self.screen_timeout)
            self._start_off_timer()

        self._publish_state()

    def _publish_state(self):
        if self.has_target:
            if self.distance == 0:
                status = "stationary"
            elif self.distance_threshold == 0 or self.last_known_distance <= self.distance_threshold:
                status = "near"
            else:
                status = "away"
        else:
            status = "away"

        self._mqtt.publish(
            "home/dashboard/kitchen/presence",
            json.dumps({
                "presence": self.has_target,
                "distance": self.distance,
                "status": status,
                "screen": "on" if self.screen_on else "off",
            }),
            retain=True,
        )

    def _turn_screen_on(self):
        if self.screen_on:
            return
        try:
            subprocess.run(
                ["vcgencmd", "display_power", "1"],
                capture_output=True, timeout=3,
            )
            self.screen_on = True
            logger.info("Screen turned ON")
        except subprocess.TimeoutExpired:
            logger.error("HDMI ON command timed out")
        except FileNotFoundError:
            logger.warning("vcgencmd not found — not running on Raspberry Pi?")
        except Exception as e:
            logger.error("Failed to turn screen on: %s", e)

    def _turn_screen_off(self):
        if not self.screen_on:
            return
        try:
            subprocess.run(
                ["vcgencmd", "display_power", "0"],
                capture_output=True, timeout=3,
            )
            self.screen_on = False
            self._off_timer = None
            self._publish_state()
            logger.info("Screen turned OFF")
        except subprocess.TimeoutExpired:
            logger.error("HDMI OFF command timed out")
        except FileNotFoundError:
            logger.warning("vcgencmd not found — not running on Raspberry Pi?")
        except Exception as e:
            logger.error("Failed to turn screen off: %s", e)

    def _start_off_timer(self):
        t = threading.Timer(self.screen_timeout, self._turn_screen_off)
        t.daemon = True
        t.start()
        self._off_timer = t

    def _cancel_timer(self):
        if self._off_timer and self._off_timer.is_alive():
            self._off_timer.cancel()
            logger.debug("Screen-off timer cancelled")
        self._off_timer = None

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT connected")
            self._publish_state()
        else:
            logger.error("MQTT connection failed (rc=%d)", rc)

    def _on_mqtt_disconnect(self, client, userdata, rc):
        logger.warning("MQTT disconnected (rc=%d)", rc)

    def shutdown(self):
        self._running = False
        self._cancel_timer()
        self._turn_screen_on()
        self._mqtt.loop_stop()
        self._mqtt.disconnect()
        logger.info("Daemon shut down — screen restored")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("paho").setLevel(logging.WARNING)

    mqtt_broker = os.environ.get("MQTT_BROKER", "localhost")
    mqtt_port = int(os.environ.get("MQTT_PORT", 1883))
    mqtt_user = os.environ.get("MQTT_USER", "")
    mqtt_pass = os.environ.get("MQTT_PASS", "")
    serial_port = os.environ.get("SERIAL_PORT", "/dev/serial0")
    screen_timeout = int(os.environ.get("SCREEN_TIMEOUT", 60))
    distance_threshold = int(os.environ.get("DISTANCE_THRESHOLD", 50))

    logger.info(
        "Starting — serial=%s timeout=%ds threshold=%dcm mqtt=%s:%s",
        serial_port, screen_timeout, distance_threshold,
        mqtt_broker, mqtt_port,
    )

    controller = PresenceController(
        mqtt_broker, mqtt_port, mqtt_user, mqtt_pass,
        screen_timeout=screen_timeout,
        distance_threshold=distance_threshold,
    )

    def signal_handler(signum, frame):
        logger.warning("Signal %d received — shutting down", signum)
        controller.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    while controller._running:
        reader = None
        try:
            reader = SerialReader(serial_port)
            logger.info("Serial connected on %s", serial_port)
            while controller._running:
                result = reader.read_frame()
                if result:
                    controller.update(*result)
                time.sleep(0.02)
        except serial.SerialException:
            logger.error("Serial error on %s — retrying in 2s", serial_port)
        except Exception:
            logger.exception("Unexpected error")
        finally:
            if reader:
                reader.close()
        if controller._running:
            time.sleep(2)

    controller.shutdown()


if __name__ == "__main__":
    main()
