import gevent
from metlog import Sun, init_db, metlog_task

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

    sensor_url = "http://%s:%d/results" % (args.addr, args.port)

    sun = Sun(51.0, -1.6)

    g = gevent.spawn(metlog_task, args.db_file, sensor_url, sun)
    gevent.joinall([g])

