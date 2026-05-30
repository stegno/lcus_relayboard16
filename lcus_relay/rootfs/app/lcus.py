#!/usr/bin/env python3
"""
LCUS Relay Board 16 Home Assistant Add-on daemon
Production-oriented version:
- Class-based design
- MQTT auto reconnect (handled by paho loop)
- Serial auto reconnect
- Watchdog polling
- MQTT Discovery (published once per startup)
- Command queue for UART serialization
- Graceful shutdown
"""

import json
import logging
import os
import queue
import re
import signal
import threading
import time

import paho.mqtt.client as mqtt
import serial

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


class LCUSRelayDaemon:

    def __init__(self):
        self.log = logging.getLogger("lcus")

        self.serial_port_name = os.environ["SERIAL_PORT"]
        self.poll_interval = int(os.environ.get("POLL_INTERVAL", "10"))

        self.mqtt_host = os.environ["MQTT_HOST"]
        self.mqtt_port = int(os.environ["MQTT_PORT"])
        self.mqtt_user = os.environ["MQTT_USER"]
        self.mqtt_password = os.environ["MQTT_PASSWORD"]

        self.running = True
        self.discovery_sent = False

        self.serial = None
        self.serial_lock = threading.Lock()

        self.command_queue = queue.Queue()

        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.mqtt.username_pw_set(
            self.mqtt_user,
            self.mqtt_password
        )

        self.mqtt.on_connect = self.on_connect
        self.mqtt.on_disconnect = self.on_disconnect
        self.mqtt.on_message = self.on_message

        self.mqtt.will_set(
            "lcus/status",
            "offline",
            retain=True
        )

    def open_serial(self):

        while self.running:

            try:

                self.log.info(
                    "Opening serial %s",
                    self.serial_port_name
                )

                self.serial = serial.Serial(
                    port=self.serial_port_name,
                    baudrate=9600,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=1,
                    xonxoff=False,
                    rtscts=False,
                    dsrdtr=False
                )

                self.serial.reset_input_buffer()
                self.serial.reset_output_buffer()

                self.log.info("Serial connected")

                return

            except Exception as e:

                self.log.warning(
                    "Serial open failed: %s",
                    e
                )

                time.sleep(5)

    def ensure_serial(self):

        if self.serial is None:
            self.open_serial()
            return

        if not self.serial.is_open:
            self.open_serial()

    def close_serial(self):

        try:
            if self.serial:
                self.serial.close()
        except Exception:
            pass

        self.serial = None

    def send_relay(self, channel, state):

        state_byte = 1 if state else 0

        checksum = (
            0xA0 +
            channel +
            state_byte
        ) & 0xFF

        packet = bytes([
            0xA0,
            channel,
            state_byte,
            checksum
        ])

        with self.serial_lock:

            try:

                self.ensure_serial()

                self.serial.write(packet)
                self.serial.flush()

                self.mqtt.publish(
                    f"lcus/relay/{channel}/state",
                    "ON" if state else "OFF",
                    retain=True
                )

                self.log.info(
                    "Relay %s -> %s",
                    channel,
                    "ON" if state else "OFF"
                )

            except Exception as e:

                self.log.error(
                    "Send failed: %s",
                    e
                )

                self.close_serial()

    def query_state(self):

        with self.serial_lock:

            try:

                self.ensure_serial()

                self.serial.reset_input_buffer()

                self.serial.write(b"\xFF")
                self.serial.flush()

                deadline = time.time() + 3

                data = b""

                while time.time() < deadline:

                    chunk = self.serial.read(256)

                    if chunk:
                        data += chunk

                    if b"CH16:" in data:
                        break

                return data.decode(
                    errors="ignore"
                )

            except Exception as e:

                self.log.error(
                    "State query failed: %s",
                    e
                )

                self.close_serial()

                return ""

    def publish_states(self):

        text = self.query_state()

        matches = re.findall(
            r"CH(\d+):\s+(ON|OFF)",
            text
        )

        if len(matches) != 16:
            return

        for ch, state in matches:

            self.mqtt.publish(
                f"lcus/relay/{ch}/state",
                state,
                retain=True
            )

    def publish_discovery(self):

        if self.discovery_sent:
            return

        for channel in range(1, 17):

            payload = {
                "name": f"Relay {channel}",
                "unique_id": f"lcus_relay_{channel}",
                "command_topic": f"lcus/relay/{channel}/set",
                "state_topic": f"lcus/relay/{channel}/state",
                "payload_on": "ON",
                "payload_off": "OFF",
                "state_on": "ON",
                "state_off": "OFF",
                "availability_topic": "lcus/status",
                "payload_available": "online",
                "payload_not_available": "offline",
                "device": {
                    "identifiers": ["lcus_t73_x16"],
                    "name": "LCUS T73 X16",
                    "manufacturer": "LCUS",
                    "model": "T73 X16 Relay Board"
                }
            }

            self.mqtt.publish(
                f"homeassistant/switch/lcus_relay_{channel}/config",
                json.dumps(payload),
                retain=True
            )

        self.discovery_sent = True
        self.log.info("MQTT Discovery published")

    def worker(self):

        while self.running:

            try:

                channel, state = self.command_queue.get(
                    timeout=1
                )

                self.send_relay(
                    channel,
                    state
                )

            except queue.Empty:
                pass

    def poller(self):

        while self.running:

            try:
                self.publish_states()
            except Exception:
                self.log.exception("Poller error")

            for _ in range(self.poll_interval):

                if not self.running:
                    return

                time.sleep(1)

    def on_connect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties
    ):

        self.log.info(
            "MQTT connected: %s",
            reason_code
        )

        client.subscribe(
            "lcus/relay/+/set"
        )

        client.publish(
            "lcus/status",
            "online",
            retain=True
        )

        self.publish_discovery()
        self.publish_states()

    def on_disconnect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties
    ):

        self.log.warning(
            "MQTT disconnected: %s",
            reason_code
        )

    def on_message(
        self,
        client,
        userdata,
        msg
    ):

        try:

            channel = int(
                msg.topic.split("/")[2]
            )

            payload = (
                msg.payload
                .decode()
                .strip()
                .upper()
            )

            if payload == "ON":
                self.command_queue.put(
                    (channel, True)
                )

            elif payload == "OFF":
                self.command_queue.put(
                    (channel, False)
                )

        except Exception:
            self.log.exception(
                "Message handling error"
            )

    def shutdown(self, *_):

        self.log.info(
            "Shutdown requested"
        )

        self.running = False

        try:
            self.mqtt.publish(
                "lcus/status",
                "offline",
                retain=True
            )
        except Exception:
            pass

        try:
            self.mqtt.disconnect()
        except Exception:
            pass

        self.close_serial()

    def run(self):

        signal.signal(
            signal.SIGINT,
            self.shutdown
        )

        signal.signal(
            signal.SIGTERM,
            self.shutdown
        )

        self.open_serial()

        threading.Thread(
            target=self.worker,
            daemon=True
        ).start()

        threading.Thread(
            target=self.poller,
            daemon=True
        ).start()

        self.mqtt.connect(
            self.mqtt_host,
            self.mqtt_port,
            keepalive=60
        )

        self.mqtt.loop_start()

        while self.running:
            time.sleep(1)

        self.mqtt.loop_stop()


if __name__ == "__main__":
    LCUSRelayDaemon().run()
