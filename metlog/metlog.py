from gevent import monkey; monkey.patch_all()
import gevent

from datetime import datetime
import sqlite3
import requests
import time

METCLOUD = "http://metcloud.freeflight.org.uk/"

def init_db(db_file):
    dbc = sqlite3.connect(db_file)
    with dbc:
        dbc.execute("create table metlog (ts timestamp, wind float, gust float, temp float)")

    dbc.close()

def metlog_task(db_file, sensor_url, sun):
    min_temp = 100
    max_temp = -100
    max_gust = 0

    now_gust = 0
    now_wind_sum = 0
    now_wind_count = 0

    sunrise_dt = sun.get_sunrise_time()
    sunset_dt = sun.get_sunset_time()

    # Update once a minute
    secs = (int(time.time()) // 60 + 1) * 60
    while 1:
        delta = secs - time.time()
        if delta > 0:
            gevent.sleep(delta)

        now_dt = datetime.utcfromtimestamp(secs)

        # Reset min/max's & sun times at midnight
        if now_dt.minute == 0 and now_dt.hour == 0:
            min_temp = 100
            max_temp = -100
            max_gust = 0

            sunrise_dt = sun.get_sunrise_time()
            sunset_dt = sun.get_sunset_time()

        # Get met sensor data
        try:
            if now_dt > sunrise_dt and now_dt < sunset_dt:
                fan = "on"
                print(now_dt, sunrise_dt, sunset_dt)
            else:
                fan = "off"

            req = requests.put(sensor_url, data={'fan': fan})
            good_req = True
        except requests.RequestException as e:
            print(str(e))
            good_req = False

        if good_req:
            data = req.json()
            temp = data.get('temp', 0)
            wind = data.get('wind', 0)
            gust = data.get('gust', 0)

            # Update database
            dbc = sqlite3.connect(db_file)
            with dbc:
                dbc.execute("insert into metlog (ts, wind, gust, temp) values (?, ?, ?, ?)",
                            (datetime.utcfromtimestamp(secs),
                             wind, gust, temp))
            dbc.close()

            # Short term averages
            now_gust = max(now_gust, gust)
            now_wind_sum += wind
            now_wind_count += 1

            # Update min/max
            min_temp = min(min_temp, temp)
            max_temp = max(max_temp, temp)
            max_gust = max(max_gust, gust)

            # Update server every five minutes
            if gmt.tm_min % 5 == 0:
                now_wind = now_wind_sum / now_wind_count

                update_server(temp, now_wind, now_gust, min_temp, max_temp,
                              max_gust)

                now_gust = 0
                now_wind_sum = 0
                now_wind_count = 0

        secs += 60

def update_server(temp, wind, gust, min_temp, max_temp, max_gust):
    try:
        requests.put(METCLOUD, json={'temp': temp,
                                     'wind': wind,
                                     'gust': gust,
                                     'min_temp': min_temp,
                                     'max_temp': max_temp,
                                     'max_gust': max_gust})
    except requests.RequestException as e:
        print(str(e))
