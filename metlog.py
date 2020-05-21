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

def metlog(db_file, sensor_url):
    min_temp = 100
    max_temp = -100
    max_gust = 0
    report_gust = 0

    # Update once a minute
    secs = (int(time.time()) // 60 + 1) * 60
    while 1:
        delta = secs - time.time()
        if delta > 0:
            gevent.sleep(delta)

        gmt = time.gmtime(secs)

        # Reset min/max's at midnight
        if gmt.tm_min == 0 and gmt.tm_hour == 0:
            min_temp = 100
            max_temp = -100
            max_gust_day = 0

        # Get met sensor data
        try:
            req = requests.get(sensor_url)
            good_req = True
        except requests.RequestException as e:
            print(str(e))
            good_req = False

        if good_req:
            data = req.json()
            temp = data.get('temperature', 0)
            wind = data.get('wind_avg', 0)
            gust = data.get('wind_gust', 0)

            # Update database
            dbc = sqlite3.connect(db_file)
            with dbc:
                dbc.execute("insert into metlog (ts, wind, gust, temp) values (?, ?, ?, ?)",
                            (datetime.utcfromtimestamp(secs),
                             wind, gust, temp))
            dbc.close()

            # Update min/max
            min_temp = min(min_temp, temp)
            max_temp = max(max_temp, temp)
            max_gust = max(max_gust, gust)
            report_gust = max(report_gust, gust)

            # Update server every 15 minutes
            if gmt.tm_min % 15 == 0:
                update_server(temp, wind, report_gust, min_temp, max_temp,
                              max_gust)
                report_gust = 0

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

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("addr", help="Met sensor address")
    parser.add_argument("db_file", help="Database file")
    parser.add_argument("--init", action="store_true",
                        help="Initialise database")
    parser.add_argument("-p", "--port", type=int, default=8000,
                        help="Met sensor port")
    args = parser.parse_args()

    if args.init:
        init_db(args.db_file)

    sensor_url = "http://%s:%d/" % (args.addr, args.port)

    g = gevent.spawn(metlog, args.db_file, sensor_url)
    gevent.joinall([g])