import time
import socket # Zum Abrufen der IP-Adresse des Raspberry Pi
import subprocess # Initialisiert die subprocess Bibliothek, um MQTT-Nachrichten über die Kommandozeile zu senden
import uuid # Zum Abrufen der MAC-Adresse des Raspberry Pi
import threading # Ermöglicht das gleichzeitige Ausführen von Funktionen
import adafruit_dht # DHT11 Sensor Bibliothek
import board
import RPi.GPIO as GPIO
from gpiozero import Button

# MQTT-Broker und Topics
MQTT_BROKER = "192.168.209.12"
MQTT_TOPIC_DISPLAY = "R209/Reihe1/Platz2/display"
MQTT_TOPIC_TEMPERATURE = "R209/Reihe1/Platz1/temperature"
MQTT_TOPIC_HUMIDITY = "R209/Reihe1/Platz1/humidity"

GPIO.setmode(GPIO.BCM)
GPIO.setup(17, GPIO.OUT)  # Senden LED

# DHT11 Sensor auf Port GPIO 5 starten
dht_device = None
try:
    dht_device = adafruit_dht.DHT11(board.D5)
    print("DHT11 auf GPIO 5 bereit")
except Exception as e:
    print(f"DHT11 konnte nicht gestartet werden: {e}")
    dht_device = None

# LED impuls beim Senden einer Nachricht an den MQTT Broker
def blink_tx_led(duration=0.1):
    GPIO.output(17, True)
    time.sleep(duration)
    GPIO.output(17, False)


# Veroffentlicht Nachricht per mosquitto_pub direkt, statt die MQTT Bibliothek zu verwenden
def publish_mqtt(topic, message):
    cmd = ["mosquitto_pub", "-h", MQTT_BROKER, "-t", topic, "-m", message]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    blink_tx_led()
    if result.returncode != 0:
        print(f"Fehler beim Veroeffentlichen auf {topic}: {result.stderr}")


def on_action_button_press():
    global held_triggered
    held_triggered = False


def on_action_button_held():
    global held_triggered
    held_triggered = True
    print("reset wird gesendet")
    publish_mqtt(MQTT_TOPIC_DISPLAY, "reset")


def on_action_button_release():
    global held_triggered
    if not held_triggered:
        print("enter wird gesendet")
        publish_mqtt(MQTT_TOPIC_DISPLAY, "enter")


held_triggered = False

# Taste auf GPIO26 initialisieren
action_button = Button(26) # Erstellt ein Button-Objekt für GPIO 26
action_button.hold_time = 3 # Definiert die Zeit in Sekunden, die die Taste gehalten werden muss, um die "held" Aktion auszulösen
action_button.when_pressed = on_action_button_press # Gibt die Funktion an, die ausgeführt wird, wenn die Taste gedrückt wird
action_button.when_held = on_action_button_held # Gibt die Funktion an, die ausgeführt wird, wenn die Taste gedrückt gehalten wird
action_button.when_released = on_action_button_release # Gibt die Funktion an, die ausgeführt wird, wenn die Taste losgelassen wird
 
# alle 10 sekunden veroeffentlich der PI seine MAC un IP addresse an den broker per mqtt
# Vorlage von Herrn Koeppen

def get_ip_address():
	s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	s.connect(("8.8.8.8", 80))
	return s.getsockname()[0]

def get_mac_address():
	return ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) for i in range(0,8*6,8)][::-1])

ipaddress = ""
macaddress = ""

while True:
	ipaddress = get_ip_address()
	macaddress = get_mac_address()
	msg = "Here is MAC "+macaddress+" with IP "+ipaddress+"!"
	cmd=["mosquitto_pub", "-h", "192.168.209.12", "-t", "pi_info", "-m", msg]
	result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,universal_newlines=True)
	print(msg)

    # Schickt alle 10 Sekunden die Temperatur und Luftfeuchte an den MQTT Broker
	if dht_device is not None: # Prüft erfolgreiche initialisierung des DHT11 Sensors
		try:
			temperature = dht_device.temperature
			humidity = dht_device.humidity
			if temperature is not None and humidity is not None: # Prüft, ob gültige Werte vom Sensor zurückgegeben wurden
				publish_mqtt(MQTT_TOPIC_TEMPERATURE, f"{temperature:.1f}")
				publish_mqtt(MQTT_TOPIC_HUMIDITY, f"{humidity:.1f}")
				print(f"Temperatur: {temperature:.1f}°C, Luftfeuchte: {humidity:.1f}%")
		except RuntimeError as e:
			print(f"Fehler beim Auslesen des Sensors: {e}")

	time.sleep(10)