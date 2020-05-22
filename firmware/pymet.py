import machine
import ujson as json
import uasyncio as asyncio

from ds18x20 import DS18X20
from onewire import OneWire

TEMP_ONEWIRE_PIN = "PE11"
TEMP_FAN_PIN = "PA6"

WIND_ADC_PIN = "PA3"

RED_LED_PIN = "PB14"
BLUE_LED_PIN = "PB7"

USER_SWITCH_PIN = "PC13"

#----------------------------------------------------------------------
# Network

def init_network(led):
    from network import LAN
    from utime import sleep

    nic = LAN()

    led.on()
    while 1:
        print("Waiting for active LAN")
        try:
            nic.active(True)
            break
        except:
            print("Error activating LAN, retrying...")
        sleep(0.5)
    led.off()

    led_value = False
    while not nic.isconnected():
        print("Waiting for network connection...")
        sleep(0.25)

        led.value(led_value)
        led_value = not led_value

    led.off()
    print("Network config", nic.ifconfig())

    return True

#----------------------------------------------------------------------
# Wind sensor

class WindSensor:
    # avg_size is number of samples to combine in a measurement
    def __init__(self, pin, avg_size):
        self.adc = machine.ADC(pin)

        # Sample average
        self.avg_size = avg_size;
        self.avg = 0
        self.avg_count = 0

        # Wind average
        self.acc = 0
        self.acc_count = 0

        self.gust = 0

    # Accumulate a single ADC sample
    def accumulate(self):
        val = self.adc.read_u16()

        # Convert to m/s, wind = ((val / 65535 * 3.3) - 0.4) * 32.4 / 1.6
        wind = val / 980.7 - 8.1

        self.avg += wind
        self.avg_count += 1

        # Add averaged sample to wind measurement
        if self.avg_count == self.avg_size:
            wind = self.avg / self.avg_count
            print("Wind:", wind)

            self.acc += wind
            self.acc_count += 1
            self.gust = max(self.gust, wind)

            self.avg = 0
            self.avg_count = 0

    def values(self):
        if self.acc_count == 0:
            wind = 0
            gust = 0
        else:
            wind = self.acc / self.acc_count
            gust = self.gust

        return wind, gust

    def result(self):
        wind, gust = self.values()

        self.acc = 0
        self.acc_count = 0
        self.gust = 0

        return wind, gust

#----------------------------------------------------------------------
# Temperature sensor

class TemperatureSensor:
    def __init__(self, ow_pin, fan_pin):
        ow = OneWire(ow_pin)
        self.ds_sensor = DS18X20(ow)
        self.roms = []

        fan_pin.value(0)
        self.fan_pin = fan_pin
        self.fan_value = 'off'

        self.acc = 0
        self.acc_count = 0

    def scan(self):
        self.roms = self.ds_sensor.scan()
        if self.roms:
            print("Found DS devices:", self.roms)
            self.ds_sensor.convert_temp()
        else:
            print("No DS devices found")

    def accumulate(self):
        if self.roms:
            t = self.ds_sensor.read_temp(self.roms[0])
            print("Temperature:", t)

            # Accumulate results
            self.acc += t
            self.acc_count += 1

            # Start next conversion
            self.ds_sensor.convert_temp()

    def value(self):
        if self.acc_count == 0:
            val = 0
        else:
            val = self.acc / self.acc_count

        return val

    def result(self):
        # Return average value and reset accumulator
        val = self.value()

        self.acc = 0
        self.acc_count = 0

        return val

    def set_fan(self, value):
        if value == 'on':
            self.fan_pin.value(1)
            self.fan_value = 'on'
        else:
            self.fan_pin.value(0)
            self.fan_value = 'off'

#----------------------------------------------------------------------
# Sensor task

async def sensor_task(wind_sensor, temperature_sensor, led):
    count = 0

    while 1:
        wind_sensor.accumulate()

        count += 1
        if count == 50:
            temperature_sensor.accumulate()
            count = 0

        await asyncio.sleep(0.1)

#----------------------------------------------------------------------
# Simple HTTP server

def set_fan(temp_sensor, req_line):
    if req_line == b"fan=on":
        print("Fan on")
        temp_sensor.set_fan('on')
    else:
        print("Fan off")
        temp_sensor.set_fan('off')

async def send_response(writer, msg):
    writer.write(msg)
    await writer.drain()

    writer.close()
    await writer.wait_closed()

def make_request_handler(wind_sensor, temp_sensor, watchdog):
    async def request_handler(reader, writer):
        req_bytes = await reader.read(500)
        req_lines = req_bytes.strip().split(b"\r\n")

        try:
            method, path, ver = req_lines[0].decode().split()
        except ValueError:
            print("Bad request")
            await send_response(writer, "HTTP/1.1 400 Bad Request\r\n")
            return

        print("Request:", method, path)

        if path == "/results":
            if method == "PUT":
                set_fan(temp_sensor, req_lines[-1])

            wind, gust = wind_sensor.result()
            results = {'temp': temp_sensor.result(),
                       'wind': wind,
                       'gust': gust,
                       'fan': temp_sensor.fan_value}

        elif path == "/values" or path == "/":
            wind, gust = wind_sensor.values()
            results = {'temp': temp_sensor.value(),
                       'wind': wind,
                       'gust': gust,
                       'fan': temp_sensor.fan_value,
                       'up_count': watchdog.up_count,
                       'reset_cause': watchdog.reset_cause}

        elif path == "/fan":
            if method == "PUT":
                set_fan(temp_sensor, req_lines[-1])
                results = {'fan': temp_sensor.fan_value}
            else:
                results = None

        else:
            results = None

        if results is None:
            await send_response(writer, "HTTP/1.1 404 Not Found\r\n\r\n\r\n")
        else:
            template = "HTTP/1.1 200 OK\r\n" \
                       "Content-Type: application/json\r\n" \
                       "Content-Length: %d\r\n" \
                       "Connection: close\r\n" \
                       "\r\n%s"

            result_str = json.dumps(results)
            response = template % (len(result_str), result_str)
            await send_response(writer, response)

    return request_handler

async def server_task(wind_sensor, temp_sensor, watchdog):
    server = await asyncio.wait_for(
            asyncio.start_server(
                make_request_handler(wind_sensor, temp_sensor, watchdog),
                '0.0.0.0',
                8000),
            None)
    print('Serving...')

    async with server:
        await server.wait_closed()

#----------------------------------------------------------------------
# Watchdog

class Watchdog():
    def __init__(self, wdt, reset_cause):
        self.wdt = wdt
        self.reset_cause = reset_cause

        self.up_count = 0
        self.server_count = 0

    def server_feed(self):
        self.server_count = 0

    async def run(self):
        while 1:
            self.up_count += 1

            # Feed the watchdog
            if self.wdt is not None:
                self.wdt.feed()

                # Reset after 5 minutes if no server queries received
                self.server_count += 1
                if self.server_count > 30:
                    print("Server watchdog reset")
                    machine.reset()

            await asyncio.sleep(10)

#----------------------------------------------------------------------

def pymet(use_watchdog=True):
    # Get result cause
    reset_cause = machine.reset_cause()
    print("Reset cause:", reset_cause)

    # Set up watchdog timer (30 second timeout)
    if use_watchdog:
        import utime as time

        pin = machine.Pin(USER_SWITCH_PIN, machine.Pin.IN)
        print("Hold USER button to disable watchdog...")

        time.sleep(1)
        if pin.value():
            print("Watchdog disabled")
            wdt = None
        else:
            wdt = machine.WDT(timeout=30000)
    else:
        wdt = None

    watchdog = Watchdog(wdt, reset_cause)
    watchdog_aw = watchdog.run()

    # Start network
    led = machine.Pin(RED_LED_PIN, machine.Pin.OUT)
    init_network(led)

    # Initialise sensors
    wind_pin = machine.Pin(WIND_ADC_PIN)
    wind_sensor = WindSensor(wind_pin, 10)

    ow_pin = machine.Pin(TEMP_ONEWIRE_PIN)
    fan_pin = machine.Pin(TEMP_FAN_PIN)
    temp_sensor = TemperatureSensor(ow_pin, fan_pin)
    temp_sensor.scan()

    # Sensor task
    sensor_led = machine.Pin(BLUE_LED_PIN, machine.Pin.OUT)
    sensor_aw = sensor_task(wind_sensor, temp_sensor, sensor_led)

    asyncio.run(asyncio.gather(
        sensor_aw,
        watchdog_aw,
        server_task(wind_sensor, temp_sensor, watchdog)))
