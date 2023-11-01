To connect via WebREPL, first reboot sensor into REPL mode:

    mosquitto_pub -h rpi -t metlog/repl -m 0

then connect with WebREPL client (https://github.com/micropython/webrepl)

    python3 webrepl_cli.py 192.168.1.106 
