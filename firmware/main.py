import machine
import uos as os
import webrepl

import pymet

WDT_DISABLE_FILE = "/flash/disable_wdt"

RED_LED_PIN = "PB14"

#----------------------------------------------------------------------
# Watchdog

def disable_wdt():
    f = open(WDT_DISABLE_FILE, "w")
    f.close()

def enable_wdt():
    try:
        os.remove(WDT_DISABLE_FILE)
    except ENOENT:
        pass

def wdt_disabled():
    try:
        f = open(WDT_DISABLE_FILE)
        f.close()
        return True
    except:
        return False

def start_wdt(timeout=30000):
    if not wdt_disabled():
        print("Starting WDT...")
        wdt = machine.WDT(timeout=timeout)
    else:
        print("WDT disabled")
        wdt = None

    return wdt

#----------------------------------------------------------------------
# Network

def init_network(led):
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
# Main program

nostart = pymet.is_nostart(True)

if not nostart:
    wdt = start_wdt()

led = pymet.Led(RED_LED_PIN)
init_network(led)

webrepl.start()

if not nostart:
    pymet.pymet(wdt)
