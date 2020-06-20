import asyncio
import signal

import gmqtt

from metlog import MqttClient, Sun, ask_exit, init_db

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("addr", help="Met sensor address")
    parser.add_argument("db_file", help="Database file")
    parser.add_argument("--init", action="store_true",
                        help="Initialise database")
    args = parser.parse_args()

    if args.init:
        init_db(args.db_file)

    sun = Sun(51.0, -1.6)

    mqtt = gmqtt.Client('metlog')
    mqtt_client = MqttClient(mqtt, args.db_file, sun)

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, ask_exit)
    loop.add_signal_handler(signal.SIGTERM, ask_exit)

    loop.run_until_complete(mqtt_client.main(args.addr))
