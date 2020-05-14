from gevent import monkey; monkey.patch_all()
import gevent

from datetime import datetime
import sqlite3
import requests
import time

METCLOUD = "http://metcloud.freeflight.org.uk/"

def init_db(dbc):
    with dbc as c:
        c.execute("create table metlog (ts timestamp, wind float, gust float, temp float)")

class MetLog:
    def __init__(self, dbc):
        self.dbc = dbc
        self.min_temp = 100
        self.max_temp = -100
        self.max_gust = 0

    def run(self, addr, port):
        url = "http://%s:%d/" % (addr, port)

        secs = int(time.time()) + 1
        while 1:
            delta = secs - time.time()
            if delta > 0:
                gevent.sleep(delta)

            if time.gmtime(secs).tm_sec == 0:
                try:
                    req = requests.get(url)
                    good_req = True
                except requests.RequestException as e:
                    print(str(e))
                    good_req = False

                if good_req:
                    data = req.json()
                    temp = data.get('temperature', 0)
                    wind = data.get('wind_avg', 0)
                    gust = data.get('wind_gust', 0)

                    with self.dbc as c:
                        c.execute("insert into metlog (ts, wind, gust, temp) values (?, ?, ?, ?)",
                                  (datetime.utcfromtimestamp(secs),
                                   wind, gust, temp))

                    self.update_server(secs, temp, wind, gust)

            secs += 1

    def update_server(self, secs, temp, wind, gust):
        self.max_temp = max(self.max_temp, temp)
        self.min_temp = min(self.min_temp, temp)
        self.max_gust = max(self.max_gust, gust)

        # Reset min and max
        gt = time.gmtime(secs)
        if gt.tm_min == 0:
            if gt.tm_hour == 3:
                max_temp = temp
            if gt.tm_hour == 15:
                min_temp = temp


        # Upload to server every 15 minutes
        if gt.tm_min % 1 == 0:
            requests.put(METCLOUD, json={'temp': temp,
                                         'wind': wind,
                                         'gust': self.max_gust})
            self.max_gust = gust


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

    dbc = sqlite3.connect(args.db_file)

    if args.init:
        init_db(dbc)

    metlog = MetLog(dbc)

    g = gevent.spawn(metlog.run, args.addr, args.port)
    gevent.joinall([g])
