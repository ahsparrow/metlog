import machine
import micropython
import ujson as json
import utime as time

from mqtt_simple import MQTTClient

from ds18x20 import DS18X20
from onewire import OneWire, OneWireError

TEMP_ONEWIRE_PIN = "PE11"
TEMP_FAN_PIN = "PA6"

WIND_ADC_PIN = "PA3"

RED_LED_PIN = "PB14"
BLUE_LED_PIN = "PB7"

USER_SWITCH_PIN = "PC13"

MQTT_SERVER = "192.168.1.100"

class Led():
    def __init__(self, pin=None):
        if pin:
            self.pin = machine.Pin(pin, machine.Pin.OUT)
        else:
            self.pin = None

        self.val = 0

    def value(self, val):
        self.val = val
        if self.pin:
            self.pin.value(val)

    def toggle(self):
        if self.val == 0:
            self.value(1)
        else:
            self.value(0)

#----------------------------------------------------------------------
# Network

def init_network(led=Led()):
    from network import LAN
    from utime import sleep

    nic = LAN()

    led.value(1)
    while 1:
        print("Waiting for active LAN")
        try:
            nic.active(True)
            break
        except:
            print("Error activating LAN, retrying...")
        sleep(0.5)
    led.value(0)

    while not nic.isconnected():
        print("Waiting for network connection...")
        sleep(0.25)

        led.toggle()

    led.value(0)
    print("Network config", nic.ifconfig())

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
            self.convert = False
        else:
            print("No DS devices found")

    def accumulate(self):
        if self.roms:
            if self.convert:
                try:
                    t = self.ds_sensor.read_temp(self.roms[0])
                    print("Temperature:", t)

                    # Accumulate results
                    self.acc += t
                    self.acc_count += 1

                except OneWireError:
                    print("One-wire read error")

            # Start next conversion
            try:
                self.ds_sensor.convert_temp()
                self.convert = True
            except OneWireError:
                print("One-wire convert error")
                self.convert  = False

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
# Watchdog

class Watchdog():
    def __init__(self, wdt, reset_cause):
        self.wdt = wdt
        self.reset_cause = reset_cause

        self.up_count = 0
        self.server_count = 0

    def server_feed(self):
        self.server_count = 0

    def feed(self):
        self.up_count += 1

        # Feed the watchdog
        if self.wdt is not None:
            self.wdt.feed()

            # Reset after 5 minutes if no server queries received
            self.server_count += 1
            if self.server_count > 30:
                print("Server watchdog reset")
                machine.reset()

#----------------------------------------------------------------------

class MetSensor:
    def __init__(self, temperature_sensor, wind_sensor, led, mqtt, watchdog):
        self.temperature_sensor = temperature_sensor
        self.wind_sensor = wind_sensor
        self.led = led
        self.mqtt = mqtt
        self.watchdog = watchdog

        self.count = 0

        # Seconds from midnight GMT
        self.sunrise = 21600
        self.sunset = 64800

    def start(self, timer):
        self.mqtt.set_callback(self.mqtt_callback)
        self.mqtt.connect()
        self.mqtt.subscribe(b"metlog/#")

        self.timer_cb_ref = self.timer_cb
        timer.init(mode=machine.Timer.PERIODIC, period= 100,
                   callback=self.timer_cb_ref)

    def timer_isr(self, t):
        micropython.schedule(self.timer_cb_ref)

    def timer_cb(self, arg):
        self.count += 1

        # Wind accumulates every 100ms
        self.wind_sensor.accumulate()

        if self.count % 50 == 0:
            # Temperature accumulates every 5s
            self.temperature_sensor.accumulate()

        # Blink the LED
        self.led.value(0 if self.count % 10 else 1)

        # Publish results once a minute
        if self.count == 600:
            wind, gust = self.wind_sensor.result()
            results = {'wind': wind,
                       'gust': gust,
                       'temp': self.temperature_sensor.value(),
                       'reset_cause': self.watchdog.reset_cause,
                       'up_count': self.watchdog.up_count,
                       'fan': self.temperature_sensor.fan_value}

            print("Publish:", results)
            self.mqtt.publish(b"metsensor/results",
                              json.dumps(results).encode('utf-8'))

            self.count = 0

        # Check for incoming MQTT data
        self.mqtt.check_msg()

        # Watchdog
        if self.count % 100 == 0:
            self.watchdog.feed()

    def mqtt_callback(self, topic, msg):
        self.watchdog.server_feed()

        parts = topic.split(b'/')
        if len(parts) != 2:
            return

        try:
            if parts[1] == b'sunrise':
                self.sunrise = int(msg)

            elif parts[1] == b'sunset':
                self.sunset = int(msg)

            elif parts[1] == b'time':
                tim = int(msg)

                if tim >= self.sunrise and tim < self.sunset:
                    self.temperature_sensor.set_fan('on')
                else:
                    self.temperature_sensor.set_fan('off')

        except ValueError:
            print("ValueError:", topic, msg)

#----------------------------------------------------------------------

def pymet(use_watchdog=True):
    # Get result cause
    reset_cause = machine.reset_cause()
    print("Reset cause:", reset_cause)

    # Set up watchdog timer (30 second timeout)
    if use_watchdog:
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

    # Start network
    led = Led(RED_LED_PIN)
    init_network(led)

    # Initialise sensors
    wind_pin = machine.Pin(WIND_ADC_PIN)
    wind_sensor = WindSensor(wind_pin, 10)

    ow_pin = machine.Pin(TEMP_ONEWIRE_PIN)
    fan_pin = machine.Pin(TEMP_FAN_PIN, machine.Pin.OUT)
    temperature_sensor = TemperatureSensor(ow_pin, fan_pin)
    temperature_sensor.scan()

    # Create sensor task
    sensor_led = Led(BLUE_LED_PIN)
    mqtt = MQTTClient("metsensor", MQTT_SERVER)

    metsensor = MetSensor(temperature_sensor, wind_sensor, sensor_led,
                          mqtt, watchdog)

    timer = machine.Timer(-1)
    metsensor.start(timer)

    print("Press CTRL-C to exit...")
    try:
        while 1:
            time.sleep(1)
    except KeyboardInterrupt:
        timer.deinit()

    return metsensor
