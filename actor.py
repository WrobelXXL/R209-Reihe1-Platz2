import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import time
import threading
import spidev


def on_connect(client, userdata, flags, rc):
    client.subscribe(MQTT_TOPIC)
    print("MQTT-Eingabe aktiv")

# LED Empfangen
def blink_led(times=1, duration=0.2):
    for _ in range(times):
        GPIO.output(27, True)
        time.sleep(duration)
        GPIO.output(27, False)
        time.sleep(duration)

# MAX7219 ist eine Bibliothek, die über SPI kommuniziert. Hier implementieren wir die grundlegenden Funktionen, um die 8x8 Matrix zu steuern.
def write_max7219(address, value):
    if spi is None:
        return
    spi.writebytes([address, value])


# Diese Funktion initialisiert das MAX7219 Display, setzt es in den Normalmodus, legt das Scan-Limit auf 8 Zeilen fest, deaktiviert Decodierung und stellt die Helligkeit ein.
def init_matrix():
    write_max7219(0x0C, 0x01)  # normal mode
    write_max7219(0x0B, 0x07)  # scan limit 8 digits
    write_max7219(0x09, 0x00)  # no decode
    write_max7219(0x0A, 0x0F)  # intensity
    clear_matrix()

# Setzt alle LEDs zurück und löscht die Anzeige. Befehl wird auch beim starten ausgeführt, damit die Anzeige immer leer beginnt.
def clear_matrix():
    for row in range(8):
        write_max7219(row + 1, 0x00)

# Zeigt Cursor und gesetzte Punkte
def update_matrix():
    for row in range(8):
        byte_val = 0
        for col in range(8):
            is_cursor_pos = col == cursor_x and row == cursor_y # Cursorposition prüfen

            if is_cursor_pos:
                if cursor_visible:
                    byte_val |= 1 << (7 - col)
            elif (col, row) in placed_dots:
                byte_val |= 1 << (7 - col)

        write_max7219(row + 1, byte_val)

# Cursor blinkt
def blink_cursor():
    global cursor_visible

    while True:
        with matrix_lock:
            cursor_visible = not cursor_visible
            update_matrix()
        time.sleep(0.3)

# MQTT Nachrichten verarbeiten und entsprechende Aktionen ausführen
def execute_draw_command(current_payload):
    global cursor_x, cursor_y, cursor_visible

    with matrix_lock:
        if current_payload == "unten":
            cursor_x = max(0, cursor_x - 1) # Cursor nach unten bewegen
            cursor_visible = True
            update_matrix()
        elif current_payload == "oben":
            cursor_x = min(7, cursor_x + 1) # Cursor nach oben bewegen
            cursor_visible = True
            update_matrix()
        elif current_payload == "links":
            cursor_y = max(0, cursor_y - 1) # Cursor nach links bewegen
            cursor_visible = True
            update_matrix()
        elif current_payload == "rechts":
            cursor_y = min(7, cursor_y + 1) # Cursor nach rechts bewegen
            cursor_visible = True
            update_matrix()
        elif current_payload == "enter":
            placed_dots.add((cursor_x, cursor_y)) # Punkt an aktueller Cursorposition setzen
            cursor_visible = True
            update_matrix()
        elif current_payload == "reset": # Anzeige zurücksetzen
            clear_matrix()
            time.sleep(0.3)
            cursor_x = 0
            cursor_y = 0
            placed_dots.clear()
            cursor_visible = True
            update_matrix()

    if current_payload in DIRECTION_COMMANDS:
        blink_led(times=1, duration=0.03)
    elif current_payload in {"enter", "reset"}: # Für Enter und Reset etwas längeres Blinken, um die Aktion visuell zu unterscheiden
        blink_led(times=1, duration=0.08)


# Vollständig KI-Generiert - Filtert doppelte Joystick Bursts fuer stabile Bewegungen
def should_accept_joystick_payload(payload):
    global joystick_last_payload, joystick_last_move_time, joystick_neutral_seen

    if payload == "mitte":
        joystick_neutral_seen = True
        joystick_last_payload = None
        return False

    if payload not in DIRECTION_COMMANDS:
        return True

    now = time.monotonic()
    since_last = now - joystick_last_move_time

    if since_last < JOYSTICK_MIN_MOVE_INTERVAL:
        return False

    if (
        payload == joystick_last_payload
        and not joystick_neutral_seen
        and since_last < JOYSTICK_REPEAT_BLOCK_INTERVAL
    ):
        return False

    joystick_last_payload = payload
    joystick_last_move_time = now
    joystick_neutral_seen = False
    return True


def on_message(client, userdata, msg):
    current_payload = msg.payload.decode("utf-8").strip().lower()

    if (
        current_payload in DIRECTION_COMMANDS
        or current_payload == "mitte"
        or current_payload in {"enter", "reset"}
    ):
        if (current_payload in DIRECTION_COMMANDS or current_payload == "mitte") and not should_accept_joystick_payload(current_payload):
            return

        print("Empfangen MQTT:", current_payload)
        execute_draw_command(current_payload)


MQTT_BROKER = "192.168.209.12"
MQTT_TOPIC = "R209/Reihe1/Platz2/display"

# Definieren der gültigen Befehle für die Bewegung des Cursors
DIRECTION_COMMANDS = {"oben", "unten", "links", "rechts"}
JOYSTICK_MIN_MOVE_INTERVAL = 0.10
JOYSTICK_REPEAT_BLOCK_INTERVAL = 0.30
joystick_last_payload = None
joystick_last_move_time = 0.0
joystick_neutral_seen = True

matrix_lock = threading.Lock()

GPIO.setmode(GPIO.BCM)
GPIO.setup(27, GPIO.OUT) # LED Empfangen

client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

# MAX7219 Display initialisieren
spi = None
try:
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 1000000
    init_matrix()
    print("MAX7219 Display initialisiert")
except Exception as e:
    print(f"MAX7219 Display konnte nicht gestartet werden: {e}")
    print("Display wird deaktiviert")

cursor_x = 0
cursor_y = 0
placed_dots = set()
cursor_visible = True

with matrix_lock:
    update_matrix()

cursor_thread = threading.Thread(target=blink_cursor, daemon=True)
cursor_thread.start()

client.connect(MQTT_BROKER, 1883, 60)
client.loop_forever()

