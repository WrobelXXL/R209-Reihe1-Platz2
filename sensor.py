import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import time
import adafruit_dht
import board
import socket
import uuid

# MQTT-Einstellungen
MQTT_BROKER = "192.168.209.12"

GPIO.setmode(GPIO.BCM)
GPIO.setup(17, GPIO.OUT)  # Senden LED

# DHT11 Sensor auf GPIO5 initialisieren
dht_device = None
try:
    dht_device = adafruit_dht.DHT11(board.D5)
    print("DHT11 auf GPIO 5 initialisiert")
except Exception as e:
    print(f"Warnung: DHT11 konnte nicht initialisiert werden: {e}")
    dht_device = None

def get_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    return s.getsockname()[0]

def get_mac_address():
    return ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) for i in range(0,8*6,8)][::-1])

def blink_tx_led(duration=0.1):
    """GPIO 17 kurz blinken lassen beim Senden"""
    GPIO.output(17, True)
    time.sleep(duration)
    GPIO.output(17, False)

def on_connect(client, userdata, flags, rc):
    print("Sensor verbunden mit Broker")

client = mqtt.Client()
client.on_connect = on_connect
client.connect(MQTT_BROKER, 1883, 60)
client.loop_start()

# alle 10 Sekunden MAC, IP, Temperatur und Luftfeuchte veröffentlichen
while True:
    # MAC und IP senden
    ipaddress = get_ip_address()
    macaddress = get_mac_address()
    msg = "Here is MAC " + macaddress + " with IP " + ipaddress + "!"
    client.publish("pi_info", msg)
    blink_tx_led()
    print(msg)

    # Temperatur und Luftfeuchte senden
    if dht_device is not None:
        try:
            temperature = dht_device.temperature
            humidity = dht_device.humidity
            if temperature is not None and humidity is not None:
                client.publish("R209/Reihe1/Platz1/temperature", f"{temperature:.1f}")
                blink_tx_led()
                client.publish("R209/Reihe1/Platz1/humidity", f"{humidity:.1f}")
                blink_tx_led()
                print(f"Temperatur: {temperature:.1f}°C, Luftfeuchte: {humidity:.1f}%")
        except RuntimeError as e:
            print(f"Fehler beim Auslesen des Sensors: {e}")

    time.sleep(10)
