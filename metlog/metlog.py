import asyncio
from datetime import datetime
import json
import requests
import sqlite3
import time

from gmqtt.mqtt.constants import MQTTv311

METCLOUD = "http://metcloud.freeflight.org.uk/"

RESULT_COUNT = 5

def init_db(db_file):
    dbc = sqlite3.connect(db_file)
    with dbc:
        dbc.execute("create table metlog (ts timestamp, wind float, gust float, temp float)")

    dbc.close()

STOP = asyncio.Event()
def ask_exit(*args):
    STOP.set()

class MqttClient:
    def __init__(self, mqtt, db_file, sun):
        self.mqtt = mqtt
        self.db_file = db_file
        self.sun = sun

        self.last_update = datetime.utcnow()
        self.update_count = 0
        self.reset_min_max()

        self.wind_sum = 0
        self.gust = 0

        self.sunrise = 0
        self.sunset = 0

        mqtt.on_connect = self.on_connect
        mqtt.on_message = self.on_message

    def reset_min_max(self):
        self.min_temp = 100
        self.max_temp = -100
        self.max_gust = 0

    def on_connect(self, client, flags, rc, properties):
        client.subscribe('metsensor/results')
        self.publish_suntimes()

    def on_message(self, client, topic, payload, qos, properties):
        result = json.loads(payload)

        ts = datetime.utcfromtimestamp(round(time.time()))
        temp = result.get('temp', 0)
        wind = result.get('wind', 0)
        gust = result.get('gust', 0)

        # Update database
        dbc = sqlite3.connect(self.db_file)
        with dbc:
            dbc.execute("insert into metlog (ts, temp, wind, gust) values (?, ?, ?, ?)",
                        (ts, temp, wind, gust))
        dbc.close()

        self.update_server(ts, temp, wind, gust)

    def update_server(self, ts, temp, wind, gust):
        # Update min/max
        if ts.day != self.last_update.day:
            # Reset at start of new day
            self.reset_min_max()

            self.publish_suntimes()

        self.last_update = ts

        self.min_temp = min(self.min_temp, temp)
        self.max_temp = max(self.max_temp, temp)
        self.max_gust = max(self.max_gust, gust)

        # Short term averaging
        self.wind_sum += wind
        self.gust = max(gust, self.gust)

        self.update_count += 1
        if self.update_count == RESULT_COUNT:
            try:
                data = {'temp': temp,
                        'wind': self.wind_sum / RESULT_COUNT,
                        'gust': self.gust,
                        'min_temp': self.min_temp,
                        'max_temp': self.max_temp,
                        'max_gust': self.max_gust}
                requests.put(METCLOUD, json=data)

            except requests.RequestException as e:
                print(str(e))

            self.update_count = 0
            self.wind_sum = 0
            self.gust = 0

            secs = ts.hour * 3600 + ts.minute * 60
            self.mqtt.publish("metlog/time", str(secs))

    def publish_suntimes(self):
        sunrise = self.sun.get_sunrise_time()
        sunset = self.sun.get_sunset_time()

        # Update sun rise/set (seconds from midnight)
        sunrise_secs = sunrise.hour * 3600 + sunrise.minute * 60
        sunset_secs = sunset.hour * 3600 + sunset.minute * 60

        self.mqtt.publish("metlog/sunrise", str(sunrise_secs), qos=1, retain=True)
        self.mqtt.publish("metlog/sunset", str(sunset_secs), qos=1, retain=True)

    async def main(self, broker_host):
        await self.mqtt.connect(broker_host, version=MQTTv311)

        await STOP.wait()
        await self.mqtt.disconnect()
