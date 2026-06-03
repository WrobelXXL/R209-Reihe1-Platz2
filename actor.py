import paho.mqtt.client as mqtt 
import paho.mqtt.publish as publish
import RPi.GPIO as GPIO 
import time
import threading
import spidev
from gpiozero import Button

def on_connect(client, userdata, flags, rc): 
    client.subscribe("R209/Reihe1/Platz2/display")

def blink_led(times=1, duration=0.2):
    """LED blinken lassen"""
    for _ in range(times):
        GPIO.output(27, True)
        time.sleep(duration)
        GPIO.output(27, False)
        time.sleep(duration)

def write_max7219(address, value):
    """Schreibe an MAX7219"""
    if spi is None:
        return
    spi.writebytes([address, value])
    
def init_matrix():
    """Initialisiere MAX7219 Matrix"""
    write_max7219(0x0C, 0x01)  # Normal mode
    write_max7219(0x0B, 0x07)  # Scan limit (8 digits)
    write_max7219(0x09, 0x00)  # No decode
    write_max7219(0x0A, 0x0F)  # Max intensity
    clear_matrix()

def clear_matrix():
    """Lösche Display"""
    for row in range(8):
        write_max7219(row + 1, 0x00)

def update_matrix():
    """Zeige Cursor und gesetzte Punkte"""
    for row in range(8):
        byte_val = 0
        for col in range(8):
            # Zeige Cursor immer wenn er aktiv ist (auch auf Punkten) - höhere Priorität
            if cursor_visible and col == cursor_x and row == cursor_y:
                byte_val |= (1 << (7 - col))
            # Zeige gesetzte Punkte
            elif (col, row) in placed_dots:
                byte_val |= (1 << (7 - col))
        write_max7219(row + 1, byte_val)

def blink_cursor():
    global cursor_visible
    while True:
        with matrix_lock:
            if not test_mode:  # Nicht blinken während Test
                cursor_visible = not cursor_visible
                update_matrix()
        time.sleep(0.3)  # Schneller blinken (0.3s statt 0.5s)

def on_message(client, userdata, msg):
    global cursor_x, cursor_y, placed_dots, test_mode, cursor_visible
    current_payload = msg.payload.decode("utf-8").lower()

    print("Empfangen:", current_payload)
    blink_led(times=1, duration=0.2)

    with matrix_lock:
        if current_payload == "unten":
            cursor_x = max(0, cursor_x - 1)
            cursor_visible = True
            update_matrix()
        elif current_payload == "oben":
            cursor_x = min(7, cursor_x + 1)
            cursor_visible = True
            update_matrix()
        elif current_payload == "links":
            cursor_y = max(0, cursor_y - 1)
            cursor_visible = True
            update_matrix()
        elif current_payload == "rechts":
            cursor_y = min(7, cursor_y + 1)
            cursor_visible = True
            update_matrix()
        elif current_payload == "enter":
            placed_dots.add((cursor_x, cursor_y))
            update_matrix()
        elif current_payload == "test":
            # Test: Alle Pixel an und aus
            test_mode = True
            for row in range(8):
                write_max7219(row + 1, 0xFF)  # Alle Pixel an
            time.sleep(3)
            clear_matrix()  # Alle Pixel aus
            time.sleep(3)
            test_mode = False
            update_matrix()  # Original-Display wiederherstellen
            print("Test: Alle Pixel an/aus")
        elif current_payload == "flush":
            # Queue leeren (aktuell: nur Nachricht loggen)
            print("Flush: Warteschlange geleert")
        elif current_payload == "reset":
            # Alles löschen
            clear_matrix()
            time.sleep(0.3)
            # Zurücksetzen: Cursor zu (0,0), alle Punkte löschen
            cursor_x = 0
            cursor_y = 0
            placed_dots.clear()
            cursor_visible = True
            update_matrix()
            print("Reset: Alles gelöscht und zurückgesetzt")
        
client = mqtt.Client() 
client.on_connect = on_connect 
client.on_message = on_message

# Lock für Thread-sichere Matrix-Aktualisierung
matrix_lock = threading.Lock()

GPIO.setmode(GPIO.BCM)
GPIO.setup(27, GPIO.OUT)  # Empfangen LED

# Button-Setup für Taste an GPIO 26
MQTT_BROKER = "192.168.209.12"
MQTT_TOPIC = "R209/Reihe1/Platz2/display"

taster = Button(26)
taster.hold_time = 3  # 3 Sekunden
held_triggered = False

def on_button_press():
    global held_triggered
    held_triggered = False

def on_button_held():
    global held_triggered
    held_triggered = True
    print("LANGER DRUCK - Reset wird gesendet...")
    # Erst Queue löschen, dann Reset
    publish.single(MQTT_TOPIC, "flush", hostname=MQTT_BROKER)
    print("MQTT gesendet: flush (Queue leeren)")
    time.sleep(0.1)
    publish.single(MQTT_TOPIC, "reset", hostname=MQTT_BROKER)
    print("MQTT gesendet: reset")

def on_button_release():
    global held_triggered
    if not held_triggered:
        print("KURZER DRUCK - Test wird gesendet...")
        publish.single(MQTT_TOPIC, "test", hostname=MQTT_BROKER)
        print("MQTT gesendet: test")

taster.when_pressed = on_button_press
taster.when_held = on_button_held
taster.when_released = on_button_release

# SPI Setup für MAX7219
spi = None
try:
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 1000000
    init_matrix()
    print("MAX7219 Display initialisiert")
except Exception as e:
    print(f"Warnung: MAX7219 Display konnte nicht initialisiert werden: {e}")
    print("Display wird deaktiviert")

# Cursor Status
cursor_x = 0
cursor_y = 0
placed_dots = set()
cursor_visible = True
test_mode = False

# Startup - Cursor zeigen
with matrix_lock:
    update_matrix()

# Cursor-Blinken in separatem Thread starten
cursor_thread = threading.Thread(target=blink_cursor, daemon=True)
cursor_thread.start()

client.connect("192.168.209.12", 1883, 60)
client.loop_forever()  # MQTT-Verbindung aktiv halten