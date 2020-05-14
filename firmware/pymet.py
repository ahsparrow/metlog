import machine
import ujson as json
import uasyncio as asyncio
import uarray as array

from ds18x20 import DS18X20
from onewire import OneWire

TEMP_ONEWIRE_PIN = "PE11"
WIND_ADC_PIN = "PA3"

RED_LED_PIN = "PB14"
YELLOW_LED_PIN = "PB0"
BLUE_LED_PIN = "PB7"

USER_SWITCH_PIN = "PC13"

WIND_STORE_LEN = 60

def init_network():
    from network import LAN
    from utime import sleep

    red_led = machine.Pin(RED_LED_PIN, machine.Pin.OUT)
    yellow_led = machine.Pin(YELLOW_LED_PIN, machine.Pin.OUT)

    nic = LAN()

    red_led.on()
    while 1:
        print("Waiting for active LAN")
        try:
            nic.active(True)
            break
        except:
            print("Error activating LAN, retrying...")
    red_led.off()

    yellow_led.on()
    while not nic.isconnected():
        print("Waiting for network connection...")
        sleep(1)

    yellow_led.off()
    print("Network config", nic.ifconfig())

    return True

async def wind_task():
    global results

    pin = machine.Pin(WIND_ADC_PIN)
    adc = machine.ADC(pin)

    led = machine.Pin(BLUE_LED_PIN, machine.Pin.OUT)

    # One minute history for average and gust speeds
    hist = array.array('f', [0] * WIND_STORE_LEN)
    idx = 0

    while 1:
        val = adc.read_u16()

        # Convert to m/s, wind = ((val / 65535 * 3.3) - 0.4) * 32.4 / 1.6
        wind = val / 980.7 - 8.1
        print("Wind:", wind)

        hist[idx] = wind
        idx = idx + 1
        if idx == WIND_STORE_LEN:
            idx = 0

        results['wind_inst'] = wind
        results['wind_avg'] = sum(hist) / WIND_STORE_LEN
        results['wind_gust'] = max(hist)

        led.off()
        await asyncio.sleep(0.95)
        led.on()
        await asyncio.sleep(0.05)

async def temperature_task():
    global results

    pin = machine.Pin(TEMP_ONEWIRE_PIN)
    ow = OneWire(pin)
    ds_sensor = DS18X20(ow)

    roms = ds_sensor.scan()
    if roms:
        print("Found DS devices:", roms)
    else:
        print("No DS devices found")
        return

    while 1:
        ds_sensor.convert_temp()
        await asyncio.sleep(5)

        t = ds_sensor.read_temp(roms[0])
        print("Temperature:", t)
        results['temperature'] = t

async def request_handler(reader, writer):
    global results, server_watchdog

    server_watchdog = 0

    data = await reader.read(500)
    message = data.decode()
    print("Received message")

    template = "HTTP/1.1 200 OK\r\n" \
               "Content-Type: application/json\r\n" \
               "Content-Length: %d\r\n" \
               "Connection: close\r\n" \
               "\r\n%s"
    result_str = json.dumps(results)

    writer.write(template % (len(result_str), result_str))
    await writer.drain()

    writer.close()
    await writer.wait_closed()

async def server_task():
    server = await asyncio.wait_for(
            asyncio.start_server(request_handler, '0.0.0.0', 8000),
            None)
    print('Serving...')

    async with server:
        await server.wait_closed()

async def watchdog_task():
    global results, server_watchdog, wdt

    count = 0
    server_watchdog = 0
    while 1:
        results['up_count'] = count
        count += 1

        if wdt is not None:
            wdt.feed()

        server_watchdog += 1
        if server_watchdog > 60:
            # Reset after 10 minutes
            print("Server watchdog reset")
            machine.reset()

        await asyncio.sleep(10)

async def main():
    await asyncio.gather(
            wind_task(),
            temperature_task(),
            server_task(),
            watchdog_task())

def run(watchdog=True):
    global results, wdt
    results = {}

    reset_cause = machine.reset_cause()
    results['reset_cause'] = reset_cause
    print("Reset cause:", reset_cause)

    if watchdog:
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

    init_network()

    asyncio.run(main())
