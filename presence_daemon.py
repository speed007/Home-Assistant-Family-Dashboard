#!/usr/bin/env python3
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
    HEADER = b"\xf4\xf3\xf2\xf1"
    FOOTER = b"\xf8\xf7\xf6\xf5"

    def __init__(self, port, baud=256000, timeout=0.1):
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
        now = time.time()
        if now - self._last_read < 1.0 / 50:
            return None
        self._last_read = now

        try:
            chunk = self.ser.read(256)
        except serial.SerialException:
            return None
        if not chunk:
            return None

        self._buf.extend(chunk)

        while len(self._buf) >= 10:
            idx = self._buf.find(self.HEADER)
            if idx == -1:
                self._buf = bytearray()
                return None
            if idx > 0:
                del self._buf[:idx]

            if len(self._buf) < 10:
                return None

            data_len = self._buf[4] | (self._buf[5] << 8)
            if data_len < 6 or data_len > 100:
                del self._buf[:6]
                continue

            total = 6 + data_len + 4
            if len(self._buf) < total:
                return None

            frame = bytes(self._buf[:total])
            del self._buf[:total]

            if frame[-4:] != self.FOOTER:
                continue

            data = frame[6:-4]

            if data[0] != 1 and data[0] != 2:
                continue
            if data[1] != 0xAA:
                continue

            bd = data[2:11]
            target_status = bd[0]
            moving_dist = bd[1] | (bd[2] << 8)
            moving_energy = bd[3]
            static_dist = bd[4] | (bd[5] << 8)
            static_energy = bd[6]
            has_target = target_status in (1, 2, 3)
            dist = moving_dist if moving_dist > 0 else static_dist

            return (has_target, dist, moving_energy, static_energy, target_status)

        return None


class PresenceController:
    def __init__(self, mqtt_broker, mqtt_port, mqtt_user, mqtt_pass,
                 screen_timeout=60, distance_threshold=50):
        self.screen_timeout = screen_timeout
        self.distance_threshold = distance_threshold
        self.has_target = False
        self.distance = 0
        self.moving_energy = 0
        self.still_energy = 0
        self.detection_state = 0
        self.last_known_distance = 0
        self.screen_on = True
        self._off_timer = None
        self._running = True
        self._energy_idle_counter = 0

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

    def update(self, has_target, distance, moving_energy, still_energy, state):
        self.moving_energy = moving_energy
        self.still_energy = still_energy
        self.detection_state = state

        real_target = has_target and (moving_energy > 0 or still_energy > 0)

        if not real_target:
            self._energy_idle_counter += 1
            if self._energy_idle_counter >= 5:
                has_target = False
        else:
            self._energy_idle_counter = 0
            has_target = True

        in_range = (
            has_target
            and distance > 0
            and (self.distance_threshold == 0 or distance <= self.distance_threshold)
        )

        if in_range:
            self.has_target = True
            self.last_known_distance = distance
            self.distance = distance
            self._cancel_timer()
            if not self.screen_on:
                self._turn_screen_on()
        else:
            if self.has_target:
                self.has_target = False
                self.distance = 0
                logger.info("Presence lost — starting %ds timer", self.screen_timeout)
                self._start_off_timer()

        logger.info(
            "target=%s dist=%d moving_e=%d still_e=%d state=%d screen=%s",
            has_target, distance, moving_energy, still_energy, state,
            "on" if self.screen_on else "off",
        )

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
                "moving_energy": self.moving_energy,
                "still_energy": self.still_energy,
                "detection_state": self.detection_state,
                "status": status,
                "screen": "on" if self.screen_on else "off",
            }),
            retain=True,
        )

    def _wlr_cmd(self, state):
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = "/run/user/1000"
        env["WAYLAND_DISPLAY"] = "wayland-0"
        try:
            subprocess.run(
                ["wlr-randr", "--output", "HDMI-A-1", "--on" if state else "--off"],
                capture_output=True, timeout=5, env=env,
            )
        except subprocess.TimeoutExpired:
            logger.error("wlr-randr timed out")
        except Exception as e:
            logger.error("wlr-randr failed: %s", e)

    def _turn_screen_on(self):
        if self.screen_on:
            return
        self._wlr_cmd(True)
        self.screen_on = True
        logger.info("Screen turned ON")

    def _turn_screen_off(self):
        if not self.screen_on:
            return
        self._wlr_cmd(False)
        self.screen_on = False
        self._off_timer = None
        self._publish_state()
        logger.info("Screen turned OFF")

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
    serial_baud = int(os.environ.get("SERIAL_BAUD", 256000))
    screen_timeout = int(os.environ.get("SCREEN_TIMEOUT", 60))
    distance_threshold = int(os.environ.get("DISTANCE_THRESHOLD", 50))

    logger.info(
        "Starting — serial=%s baud=%d timeout=%ds threshold=%dcm mqtt=%s:%s",
        serial_port, serial_baud, screen_timeout, distance_threshold,
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
            reader = SerialReader(serial_port, baud=serial_baud)
            logger.info("Serial connected on %s @ %d baud", serial_port, serial_baud)
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
