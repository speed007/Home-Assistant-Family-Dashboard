#!/usr/bin/env python3
import json
import logging
import os
import signal
import sys
import threading
import time

import serial
import paho.mqtt.client as mqtt

logger = logging.getLogger("presence_daemon")

NUM_GATES = 9


class SerialReader:
    HEADER = b"\xf4\xf3\xf2\xf1"
    FOOTER = b"\xf8\xf7\xf6\xf5"
    CONFIG_HEADER = b"\xfd\xfc\xfb\xfa"
    CONFIG_FOOTER = b"\x04\x03\x02\x01"

    def __init__(self, port, baud=256000, timeout=0.1, max_gate=2):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.max_gate = max_gate
        self._buf = bytearray()
        self._last_read = 0.0
        self.ser = None
        self._connect()

    def _send_cmd(self, cmd):
        if self.ser and self.ser.is_open:
            self.ser.write(cmd)
            self.ser.flush()

    def _recv_reply(self):
        end = time.time() + 0.3
        buf = bytearray()
        while time.time() < end:
            chunk = self.ser.read(256)
            if chunk:
                buf.extend(chunk)
        for hdr_pos in range(len(buf)):
            if buf[hdr_pos:hdr_pos+4] == self.CONFIG_HEADER:
                frame_end = buf.find(self.CONFIG_FOOTER, hdr_pos)
                if frame_end != -1:
                    return bytes(buf[hdr_pos:frame_end+4])
                else:
                    return bytes(buf[hdr_pos:])
        return None

    def _build_cmd(self, cmd_code, data=b''):
        payload = bytes([cmd_code, 0x00]) + data
        length = len(payload).to_bytes(2, 'little')
        return self.CONFIG_HEADER + length + payload + self.CONFIG_FOOTER

    def configure_gates(self):
        import struct
        logger.info("Configuring LD2420 gates (max gate=%d)", self.max_gate)

        self.ser.reset_input_buffer()
        time.sleep(0.5)

        cmd = self._build_cmd(0xFF, struct.pack('<H', 1))
        self._send_cmd(cmd)
        resp = self._recv_reply()
        if resp:
            logger.info("Config enable: %s", resp.hex())
        else:
            logger.warning("Config enable: no reply — retrying")
            time.sleep(0.2)
            self.ser.reset_input_buffer()
            self._send_cmd(cmd)
            resp = self._recv_reply()
            if resp:
                logger.info("Config enable (retry): %s", resp.hex())
            else:
                logger.warning("Config enable: still no reply")

        max_g = max(2, self.max_gate)
        data = struct.pack('<H', 0) + struct.pack('<I', max_g)
        data += struct.pack('<H', 1) + struct.pack('<I', max_g)
        data += struct.pack('<H', 2) + struct.pack('<I', 5)
        cmd = self._build_cmd(0x60, data)
        self._send_cmd(cmd)
        resp = self._recv_reply()
        if resp and len(resp) >= 10:
            logger.info("Max gate set: status=%02x%02x", resp[8], resp[9])

        ok = 0
        for gate in range(9):
            if gate == 0:
                moving = 5
                static = 5
            elif gate < self.max_gate:
                moving = 200
                static = 200
            else:
                moving = 255
                static = 255
            data = struct.pack('<H', 0) + struct.pack('<I', gate)
            data += struct.pack('<H', 1) + struct.pack('<I', moving)
            data += struct.pack('<H', 2) + struct.pack('<I', static)
            cmd = self._build_cmd(0x64, data)
            self._send_cmd(cmd)
            resp = self._recv_reply()
            if resp and len(resp) >= 10 and resp[8] == 0 and resp[9] == 0:
                ok += 1
        logger.info("Gate sensitivity: %d/9 OK", ok)

        cmd = self._build_cmd(0xFE)
        self._send_cmd(cmd)
        resp = self._recv_reply()
        if resp:
            logger.info("Config end: %s", resp.hex())
        else:
            logger.warning("Config end: no reply")

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

            is_engineering = data[0] == 2
            self._frame_type = data[0]
            bd = data[2:11]
            target_status = bd[0]
            moving_dist = bd[1] | (bd[2] << 8)
            moving_energy = bd[3]
            static_dist = bd[4] | (bd[5] << 8)
            static_energy = bd[6]
            has_target = target_status in (1, 2, 3) and (moving_energy > 0 or static_energy > 0)
            dist = moving_dist if moving_dist > 0 else static_dist

            moving_gates = []
            static_gates = []
            if is_engineering and len(data) >= 29:
                moving_gates = list(data[11:20])
                static_gates = list(data[20:29])

            return (has_target, dist, moving_energy, static_energy,
                    target_status, moving_gates, static_gates)

        return None


class PresenceController:
    def __init__(self, mqtt_broker, mqtt_port, mqtt_user, mqtt_pass,
                 distance_threshold=200, still_energy_threshold=50,
                 confirm_frames=10, release_frames=10,
                 discovery_prefix="homeassistant", device_id="ld2420_kitchen"):
        self.distance_threshold = distance_threshold
        self.still_energy_threshold = still_energy_threshold
        self.confirm_frames = confirm_frames
        self.release_frames = release_frames
        self.discovery_prefix = discovery_prefix
        self.device_id = device_id

        self.has_target = False
        self.distance = 0
        self.moving_energy = 0
        self.still_energy = 0
        self.detection_state = 0
        self.last_known_distance = 0
        self.last_detection_time = 0.0
        self.screen_on = True
        self.moving_gate_energies = [0] * NUM_GATES
        self.static_gate_energies = [0] * NUM_GATES
        self._running = True
        self._detect_counter = 0
        self._release_counter = 0
        self._last_pub = {}
        self._discovery_sent = False

        self._mqtt = mqtt.Client()
        if mqtt_user:
            self._mqtt.username_pw_set(mqtt_user, mqtt_pass)
        self._mqtt.on_connect = self._on_mqtt_connect
        self._mqtt.on_disconnect = self._on_mqtt_disconnect
        self._mqtt.on_message = self._on_mqtt_message

        try:
            self._mqtt.connect(mqtt_broker, mqtt_port, 60)
            self._mqtt.loop_start()
            logger.info(f"MQTT connecting to {mqtt_broker}:{mqtt_port}")
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")

    def _setup_discovery(self):
        if self._discovery_sent:
            return
        device = {
            "identifiers": [self.device_id],
            "name": "LD2420 Kitchen Sensor",
            "manufacturer": "Hi-Link",
            "model": "LD2420",
        }
        base_topic = "home/dashboard/kitchen/presence"

        entities = [
            ("binary_sensor", "kitchen_presence_ld2420", {
                "name": "Kitchen Presence",
                "unique_id": "kitchen_presence_ld2420",
                "state_topic": base_topic,
                "value_template": "{{ 'ON' if value_json.presence else 'OFF' }}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "device": device,
            }),
            ("sensor", "kitchen_distance_ld2420", {
                "name": "Kitchen Distance",
                "unique_id": "kitchen_distance_ld2420",
                "state_topic": base_topic,
                "value_template": "{{ value_json.distance }}",
                "unit_of_measurement": "cm",
                "device": device,
            }),
            ("sensor", "kitchen_moving_energy_ld2420", {
                "name": "Kitchen Moving Energy",
                "unique_id": "kitchen_moving_energy_ld2420",
                "state_topic": base_topic,
                "value_template": "{{ value_json.moving_energy }}",
                "device": device,
            }),
            ("sensor", "kitchen_still_energy_ld2420", {
                "name": "Kitchen Still Energy",
                "unique_id": "kitchen_still_energy_ld2420",
                "state_topic": base_topic,
                "value_template": "{{ value_json.still_energy }}",
                "device": device,
            }),
            ("sensor", "kitchen_detection_state_ld2420", {
                "name": "Kitchen Detection State",
                "unique_id": "kitchen_detection_state_ld2420",
                "state_topic": base_topic,
                "value_template": "{{ value_json.detection_state }}",
                "device": device,
            }),
            ("sensor", "kitchen_status_ld2420", {
                "name": "Kitchen Status",
                "unique_id": "kitchen_status_ld2420",
                "state_topic": base_topic,
                "value_template": "{{ value_json.status }}",
                "device": device,
            }),
        ]

        for g in range(min(3, NUM_GATES)):
            entities.append(
                ("sensor", f"kitchen_gate_{g}_moving_ld2420", {
                    "name": f"Kitchen Gate {g} Moving Energy",
                    "unique_id": f"kitchen_gate_{g}_moving_ld2420",
                    "state_topic": base_topic,
                    "value_template": f"{{{{ value_json.gate_{g}_moving }}}}",
                    "device": device,
                })
            )
            entities.append(
                ("sensor", f"kitchen_gate_{g}_still_ld2420", {
                    "name": f"Kitchen Gate {g} Still Energy",
                    "unique_id": f"kitchen_gate_{g}_still_ld2420",
                    "state_topic": base_topic,
                    "value_template": f"{{{{ value_json.gate_{g}_still }}}}",
                    "device": device,
                })
            )

        for component, obj_id, config in entities:
            topic = f"{self.discovery_prefix}/{component}/{obj_id}/config"
            self._mqtt.publish(topic, json.dumps(config), retain=True)

        self._discovery_sent = True
        logger.info("MQTT Discovery published (%d entities)", len(entities))

    def update(self, has_target, distance, moving_energy, still_energy, state,
               moving_gates=None, static_gates=None):
        self.moving_energy = moving_energy
        self.still_energy = still_energy
        self.detection_state = state
        if moving_gates:
            self.moving_gate_energies = list(moving_gates) + [0] * max(0, NUM_GATES - len(moving_gates))
        if static_gates:
            self.static_gate_energies = list(static_gates) + [0] * max(0, NUM_GATES - len(static_gates))

        valid_target = has_target and (moving_energy >= 20 or still_energy >= self.still_energy_threshold)

        if valid_target:
            self._release_counter = 0
            self._detect_counter += 1
        else:
            self._detect_counter = 0
            self._release_counter += 1

        if self._detect_counter >= self.confirm_frames:
            self.has_target = True
            self.last_detection_time = time.time()
            self.last_known_distance = distance
            self.distance = distance
        elif self._release_counter >= self.release_frames:
            self.has_target = False
            self.distance = 0

        self._log_sampler = getattr(self, '_log_sampler', 0) + 1
        if self._log_sampler % 10 == 0 or self._changed("_lp_target", self.has_target):
            info = ("target=%s dist=%d moving_e=%d still_e=%d state=%d screen=%s"
                    % (self.has_target, distance, moving_energy, still_energy, state,
                       "on" if self.screen_on else "off"))
            if moving_gates:
                info += " mg0=%d mg1=%d mg2=%d" % tuple(
                    (moving_gates + [0]*3)[:3])
            logger.info(info)

        self._publish_on_change()

    def _changed(self, attr, value):
        old = self._last_pub.get(attr)
        if old != value:
            self._last_pub[attr] = value
            return True
        return False

    def _publish_on_change(self):
        gate_changed = False
        for g in range(3):
            if self._changed(f"_p_g{g}_m", self.moving_gate_energies[g]) or \
               self._changed(f"_p_g{g}_s", self.static_gate_energies[g]):
                gate_changed = True

        if not self._changed("_p_has_target", self.has_target) and \
           not self._changed("_p_screen", self.screen_on) and \
           not self._changed("_p_state", self.detection_state):
            if not gate_changed:
                return
            now = time.time()
            if now - getattr(self, '_last_gate_pub', 0) < 1.0:
                return
            self._last_gate_pub = now

        if self.has_target:
            if self.distance == 0:
                status = "stationary"
            elif self.distance_threshold == 0 or self.last_known_distance <= self.distance_threshold:
                status = "near"
            else:
                status = "away"
        else:
            status = "away"

        payload = {
            "presence": self.has_target,
            "distance": self.distance,
            "moving_energy": self.moving_energy,
            "still_energy": self.still_energy,
            "detection_state": self.detection_state,
            "status": status,
            "screen": "on" if self.screen_on else "off",
        }
        for g in range(min(3, NUM_GATES)):
            payload[f"gate_{g}_moving"] = self.moving_gate_energies[g]
            payload[f"gate_{g}_still"] = self.static_gate_energies[g]

        self._mqtt.publish(
            "home/dashboard/kitchen/presence",
            json.dumps(payload),
            retain=True,
        )

    def _set_screen(self, on):
        self.screen_on = on
        self._publish_on_change()
        logger.info("Screen turned %s (MQTT command)", "ON" if on else "OFF")

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT connected")
            client.subscribe("home/dashboard/kitchen/screen/set")
            self._setup_discovery()
            self._last_pub.clear()
            self._publish_on_change()
        else:
            logger.error("MQTT connection failed (rc=%d)", rc)

    def _on_mqtt_disconnect(self, client, userdata, rc):
        self._discovery_sent = False
        logger.warning("MQTT disconnected (rc=%d)", rc)

    def _on_mqtt_message(self, client, userdata, msg):
        payload = msg.payload.decode().strip().upper()
        if msg.topic == "home/dashboard/kitchen/screen/set":
            if payload == "ON":
                self._set_screen(True)
            elif payload == "OFF":
                self._set_screen(False)

    def shutdown(self):
        self._running = False
        self._mqtt.loop_stop()
        self._mqtt.disconnect()
        logger.info("Daemon shut down")


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
    distance_threshold = int(os.environ.get("DISTANCE_THRESHOLD", 200))
    still_energy_threshold = int(os.environ.get("STILL_ENERGY_THRESHOLD", 50))
    confirm_frames = int(os.environ.get("CONFIRM_FRAMES", 10))
    release_frames = int(os.environ.get("RELEASE_FRAMES", 10))
    max_gate = int(os.environ.get("MAX_GATE", 2))
    discovery_prefix = os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
    device_id = os.environ.get("MQTT_DEVICE_ID", "ld2420_kitchen")

    logger.info(
        "Starting — serial=%s baud=%d threshold=%dcm mqtt=%s:%s "
        "confirm=%d release=%d gate=%d still_thresh=%d disc=%s",
        serial_port, serial_baud, distance_threshold,
        mqtt_broker, mqtt_port, confirm_frames, release_frames,
        max_gate, still_energy_threshold, discovery_prefix,
    )

    controller = PresenceController(
        mqtt_broker, mqtt_port, mqtt_user, mqtt_pass,
        distance_threshold=distance_threshold,
        still_energy_threshold=still_energy_threshold,
        confirm_frames=confirm_frames,
        release_frames=release_frames,
        discovery_prefix=discovery_prefix,
        device_id=device_id,
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
            reader = SerialReader(serial_port, baud=serial_baud, timeout=0.02, max_gate=max_gate)
            logger.info("Serial connected on %s @ %d baud", serial_port, serial_baud)
            reader.configure_gates()
            while controller._running:
                result = reader.read_frame()
                if result:
                    controller.update(*result)
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
