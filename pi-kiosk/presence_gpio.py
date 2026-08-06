#!/usr/bin/env python3
import json, logging, os, signal, subprocess, sys, time
import paho.mqtt.client as mqtt

logger = logging.getLogger("presence_gpio")
OT1_PIN = 17

def gpio_read(pin):
    r = subprocess.run(["pinctrl", "get", str(pin)], capture_output=True, text=True, timeout=3)
    return "hi" in r.stdout.lower()

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    mqtt_broker = os.environ.get("MQTT_BROKER", "192.168.102.112")
    mqtt_port = int(os.environ.get("MQTT_PORT", 1883))
    mqtt_user = os.environ.get("MQTT_USER", "")
    mqtt_pass = os.environ.get("MQTT_PASS", "")
    screen_timeout = int(os.environ.get("SCREEN_TIMEOUT", 60))

    mqttc = mqtt.Client()
    if mqtt_user:
        mqttc.username_pw_set(mqtt_user, mqtt_pass)
    mqttc.connect(mqtt_broker, mqtt_port, 60)
    mqttc.loop_start()
    logger.info("MQTT connected to %s:%s", mqtt_broker, mqtt_port)

    logger.info("Monitoring OT1 on GPIO%d", OT1_PIN)
    last_presence = 0
    screen_on = True
    running = True

    def shutdown():
        nonlocal running
        running = False
        if not screen_on:
            subprocess.run(["vcgencmd", "display_power", "1"], capture_output=True)
        mqttc.loop_stop()
        mqttc.disconnect()
        logger.info("Shutdown")

    signal.signal(signal.SIGTERM, lambda *a: shutdown())
    signal.signal(signal.SIGINT, lambda *a: shutdown())

    while running:
        present = gpio_read(OT1_PIN)
        now = time.time()
        if present:
            last_presence = now
            if not screen_on:
                subprocess.run(["vcgencmd", "display_power", "1"], capture_output=True)
                screen_on = True
                logger.info("Presence - screen ON")
        elif screen_on and (now - last_presence) > screen_timeout:
            subprocess.run(["vcgencmd", "display_power", "0"], capture_output=True)
            screen_on = False
            logger.info("No presence %ds - screen OFF", screen_timeout)

        mqttc.publish("home/dashboard/kitchen/presence", json.dumps({
            "presence": bool(present), "screen": "on" if screen_on else "off",
        }), retain=True)
        time.sleep(0.5)

if __name__ == "__main__":
    main()
