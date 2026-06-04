import paho.mqtt.client as mqtt 
import paho.mqtt.publish as publish
import RPi.GPIO as GPIO 
import time
import threading
import spidev
import os
import sys
import subprocess
import atexit
import select
import termios
import tty
from gpiozero import Button

def on_connect(client, userdata, flags, rc): 
    if USE_MQTT_INPUT:
        client.subscribe("R209/Reihe1/Platz2/display")
        print("MQTT-Eingabe aktiv")
    else:
        print("MQTT-Eingabe deaktiviert (lokale Tastatursteuerung aktiv)")

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
            is_cursor_pos = (col == cursor_x and row == cursor_y)

            # Cursor hat Priorität: in der "aus"-Phase bleibt die Cursor-Position aus,
            # auch wenn dort bereits ein Punkt gesetzt wurde.
            if is_cursor_pos:
                if cursor_visible:
                    byte_val |= (1 << (7 - col))
            # Gesetzte Punkte (außerhalb der Cursor-Position) immer anzeigen
            elif (col, row) in placed_dots:
                byte_val |= (1 << (7 - col))
        write_max7219(row + 1, byte_val)

def blink_cursor():
    global cursor_visible
    while True:
        with matrix_lock:
            # Nur im Malen-Modus blinken, damit Snake die Matrix exklusiv nutzen kann.
            if control_mode == MODE_MALEN and not test_mode:
                cursor_visible = not cursor_visible
                update_matrix()
        time.sleep(0.3)  # Schneller blinken (0.3s statt 0.5s)


def execute_draw_command(current_payload):
    global cursor_x, cursor_y, placed_dots, test_mode, cursor_visible

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

    if current_payload in DIRECTION_COMMANDS:
        blink_led(times=1, duration=0.03)
    elif current_payload in {"enter", "test", "reset"}:
        blink_led(times=1, duration=0.08)

def on_message(client, userdata, msg):
    if not USE_MQTT_INPUT:
        return

    current_payload = msg.payload.decode("utf-8").strip().lower()

    # In Snake-Modus reagiert Actor nicht auf Zeichen-Befehle.
    if control_mode == MODE_SNAKE:
        return

    # Joystick-Richtungen glätten, damit doppelte Bursts nicht alles ueberfahren.
    if (current_payload in DIRECTION_COMMANDS or current_payload == "mitte") and not should_accept_joystick_payload(current_payload):
        return

    print("Empfangen MQTT:", current_payload)
    execute_draw_command(current_payload)
        
client = mqtt.Client() 
client.on_connect = on_connect 
client.on_message = on_message

# Lock für Thread-sichere Matrix-Aktualisierung
matrix_lock = threading.Lock()

GPIO.setmode(GPIO.BCM)
GPIO.setup(27, GPIO.OUT)  # Empfangen LED

# Button-Setup
MQTT_BROKER = "192.168.209.12"
MQTT_TOPIC = "R209/Reihe1/Platz2/display"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SNAKE_SCRIPT_PATH = os.path.join(BASE_DIR, "snake.py")

MODE_MALEN = "malen"
MODE_SNAKE = "snake"
control_mode = MODE_MALEN
USE_MQTT_INPUT = False
USE_LOCAL_KEYBOARD_INPUT = True

# Joystick-Stabilisierung fuer MQTT-Richtungsdaten
DIRECTION_COMMANDS = {"oben", "unten", "links", "rechts"}
JOYSTICK_MIN_MOVE_INTERVAL = 0.10      # Max. 10 Schritte/Sek.
JOYSTICK_REPEAT_BLOCK_INTERVAL = 0.30  # Doppelte gleiche Richtung bremsen
joystick_last_payload = None
joystick_last_move_time = 0.0
joystick_neutral_seen = True
MODE_TOGGLE_COOLDOWN = 0.35
last_mode_toggle_time = 0.0
COMBO_SIMULTANEOUS_WINDOW = 0.18
btn20_is_pressed = False
btn21_is_pressed = False
btn20_pressed_at = 0.0
btn21_pressed_at = 0.0
btn20_skip_release = False
btn21_skip_release = False
snake_process = None
KEYBOARD_MIN_COMMAND_INTERVAL = 0.06
keyboard_last_command_time = 0.0

# GPIO19 robust initialisieren: Polaritaet je nach Verdrahtung erkennen.
mode_button = Button(19, pull_up=True, bounce_time=0.12)          # Modus wechseln
time.sleep(0.03)
if mode_button.is_pressed:
    mode_button.close()
    mode_button = Button(19, pull_up=False, bounce_time=0.12)
    print("GPIO19: pull_up=False aktiv (Auto-Erkennung)")
else:
    print("GPIO19: pull_up=True aktiv (Auto-Erkennung)")

turn_cw_button = Button(20, bounce_time=0.05)
turn_ccw_button = Button(21, bounce_time=0.05)
action_button = Button(26)                          # Bestehende Aktions-Taste
action_button.hold_time = 3  # 3 Sekunden

held_triggered = False


def move_cursor_malen(direction):
    global cursor_x, cursor_y, cursor_visible
    if direction == "unten":
        cursor_x = max(0, cursor_x - 1)
    elif direction == "oben":
        cursor_x = min(7, cursor_x + 1)
    elif direction == "links":
        cursor_y = max(0, cursor_y - 1)
    elif direction == "rechts":
        cursor_y = min(7, cursor_y + 1)
    cursor_visible = True
    update_matrix()


def set_current_dot():
    global cursor_visible
    placed_dots.add((cursor_x, cursor_y))
    cursor_visible = True
    update_matrix()


def handle_malen_combo(second_button):
    global btn20_skip_release, btn21_skip_release

    now = time.monotonic()
    if second_button == 20:
        delta = now - btn21_pressed_at
        with matrix_lock:
            if delta <= COMBO_SIMULTANEOUS_WINDOW:
                set_current_dot()
                print("Malen: Punkt gesetzt (20+21)")
            else:
                move_cursor_malen("links")
                print("Malen: links (21 halten + 20 druecken)")
    else:
        delta = now - btn20_pressed_at
        with matrix_lock:
            if delta <= COMBO_SIMULTANEOUS_WINDOW:
                set_current_dot()
                print("Malen: Punkt gesetzt (20+21)")
            else:
                move_cursor_malen("rechts")
                print("Malen: rechts (20 halten + 21 druecken)")

    btn20_skip_release = True
    btn21_skip_release = True


# Extra Finetuning
def should_accept_joystick_payload(payload):
    """Filtert doppelte Joystick-Bursts, damit Malen stabil bleibt."""
    global joystick_last_payload, joystick_last_move_time, joystick_neutral_seen

    if payload == "mitte":
        joystick_neutral_seen = True
        joystick_last_payload = None
        return False

    if payload not in DIRECTION_COMMANDS:
        return True

    now = time.monotonic()
    since_last = now - joystick_last_move_time

    # Harte Schrittbegrenzung gegen Input-Flood.
    if since_last < JOYSTICK_MIN_MOVE_INTERVAL:
        return False

    # Gleiche Richtung ohne neutralen Mittelwert nur gedrosselt akzeptieren.
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


def read_keyboard_sequence(stream):
    """Liest eine Taste inkl. Pfeiltasten-Sequenzen."""
    first = stream.read(1)
    if first != "\x1b":
        return first

    ready, _, _ = select.select([stream], [], [], 0.01)
    if not ready:
        return first

    second = stream.read(1)
    if second != "[":
        return first + second

    ready, _, _ = select.select([stream], [], [], 0.01)
    if not ready:
        return first + second

    third = stream.read(1)
    return f"\x1b[{third}"


def handle_local_keyboard_command(command):
    global keyboard_last_command_time

    now = time.monotonic()
    if now - keyboard_last_command_time < KEYBOARD_MIN_COMMAND_INTERVAL:
        return
    keyboard_last_command_time = now

    if control_mode == MODE_MALEN:
        execute_draw_command(command)
        print(f"Tastatur: {command}")
    else:
        if command == "links":
            publish.single(MQTT_TOPIC, "ccw", hostname=MQTT_BROKER)
            print("Snake (Tastatur): ccw gesendet")
        elif command == "rechts":
            publish.single(MQTT_TOPIC, "cw", hostname=MQTT_BROKER)
            print("Snake (Tastatur): cw gesendet")


def local_keyboard_loop():
    """Lokale Steuerung ohne MQTT: Pfeile/WASD und Enter."""
    if not USE_LOCAL_KEYBOARD_INPUT:
        return

    stream = None
    should_close_stream = False
    old_terminal_settings = None
    fd = None

    try:
        if sys.stdin.isatty():
            stream = sys.stdin
        else:
            stream = open("/dev/tty", "r")
            should_close_stream = True

        fd = stream.fileno()
        old_terminal_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        print("Lokale Tastatur aktiv: Pfeile/WASD bewegen, Enter/Space setzt Punkt, m wechselt Modus")

        while True:
            ready, _, _ = select.select([stream], [], [], 0.1)
            if not ready:
                continue

            key = read_keyboard_sequence(stream)
            key_lower = key.lower() if len(key) == 1 else key
            command = None

            if key == "\x1b[A" or key_lower == "w":
                command = "oben"
            elif key == "\x1b[B" or key_lower == "s":
                command = "unten"
            elif key == "\x1b[D" or key_lower == "a":
                command = "links"
            elif key == "\x1b[C" or key_lower == "d":
                command = "rechts"
            elif key in ("\r", "\n", " "):
                command = "enter"
            elif key_lower == "m":
                on_mode_button_press()
                continue
            elif key_lower == "t":
                command = "test"
            elif key_lower == "x":
                command = "reset"

            if command is not None:
                handle_local_keyboard_command(command)

    except FileNotFoundError:
        print("Lokale Tastatursteuerung nicht gestartet: /dev/tty nicht verfuegbar")
    except Exception as e:
        print(f"Fehler in lokaler Tastatursteuerung: {e}")
    finally:
        if old_terminal_settings is not None and fd is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_terminal_settings)
            except Exception:
                pass
        if should_close_stream and stream is not None:
            stream.close()


def start_snake_process():
    global snake_process

    if snake_process is not None and snake_process.poll() is None:
        print(f"Snake laeuft bereits (PID {snake_process.pid})")
        return True

    if not os.path.exists(SNAKE_SCRIPT_PATH):
        print(f"Snake-Datei nicht gefunden: {SNAKE_SCRIPT_PATH}")
        return False

    try:
        snake_process = subprocess.Popen([sys.executable, SNAKE_SCRIPT_PATH])
        print(f"Snake gestartet (PID {snake_process.pid})")
        return True
    except Exception as e:
        snake_process = None
        print(f"Fehler beim Start von snake.py: {e}")
        return False


def stop_snake_process():
    global snake_process

    if snake_process is None:
        return

    if snake_process.poll() is not None:
        snake_process = None
        return

    try:
        snake_process.terminate()
        snake_process.wait(timeout=2)
        print("Snake gestoppt")
    except Exception:
        try:
            snake_process.kill()
            snake_process.wait(timeout=1)
            print("Snake hart gestoppt")
        except Exception as e:
            print(f"Fehler beim Stoppen von snake.py: {e}")
    finally:
        snake_process = None


def on_mode_button_press():
    global control_mode, last_mode_toggle_time, cursor_visible

    now = time.monotonic()
    if now - last_mode_toggle_time < MODE_TOGGLE_COOLDOWN:
        return
    last_mode_toggle_time = now

    if control_mode == MODE_MALEN:
        if start_snake_process():
            control_mode = MODE_SNAKE
            blink_led(times=2, duration=0.05)
            print("Modus gewechselt: snake")
        else:
            control_mode = MODE_MALEN
            blink_led(times=3, duration=0.04)
            print("Modus bleibt: malen (snake.py nicht gestartet)")
    else:
        stop_snake_process()
        control_mode = MODE_MALEN
        with matrix_lock:
            cursor_visible = True
            update_matrix()
        blink_led(times=1, duration=0.08)
        print("Modus gewechselt: malen")


def on_turn_cw_press():
    global btn20_is_pressed, btn20_pressed_at, btn20_skip_release
    btn20_is_pressed = True
    btn20_pressed_at = time.monotonic()
    btn20_skip_release = False

    if control_mode == MODE_MALEN and btn21_is_pressed:
        handle_malen_combo(second_button=20)


def on_turn_cw_release():
    global btn20_is_pressed, btn20_skip_release
    btn20_is_pressed = False

    if control_mode == MODE_MALEN:
        if btn20_skip_release:
            btn20_skip_release = False
            return

        with matrix_lock:
            move_cursor_malen("unten")
        print("Malen: unten (20)")
    else:
        publish.single(MQTT_TOPIC, "cw", hostname=MQTT_BROKER)
        print("Snake: cw gesendet")


def on_turn_ccw_press():
    global btn21_is_pressed, btn21_pressed_at, btn21_skip_release
    btn21_is_pressed = True
    btn21_pressed_at = time.monotonic()
    btn21_skip_release = False

    if control_mode == MODE_MALEN and btn20_is_pressed:
        handle_malen_combo(second_button=21)


def on_turn_ccw_release():
    global btn21_is_pressed, btn21_skip_release
    btn21_is_pressed = False

    if control_mode == MODE_MALEN:
        if btn21_skip_release:
            btn21_skip_release = False
            return

        with matrix_lock:
            move_cursor_malen("oben")
        print("Malen: oben (21)")
    else:
        publish.single(MQTT_TOPIC, "ccw", hostname=MQTT_BROKER)
        print("Snake: ccw gesendet")

def on_action_button_press():
    global held_triggered
    held_triggered = False

def on_action_button_held():
    global held_triggered
    held_triggered = True
    print("LANGER DRUCK - Reset wird gesendet...")
    # Erst Queue löschen, dann Reset
    publish.single(MQTT_TOPIC, "flush", hostname=MQTT_BROKER)
    print("MQTT gesendet: flush (Queue leeren)")
    time.sleep(0.1)
    publish.single(MQTT_TOPIC, "reset", hostname=MQTT_BROKER)
    print("MQTT gesendet: reset")

def on_action_button_release():
    global held_triggered
    if not held_triggered:
        print("KURZER DRUCK - Test wird gesendet...")
        publish.single(MQTT_TOPIC, "test", hostname=MQTT_BROKER)
        print("MQTT gesendet: test")


atexit.register(stop_snake_process)

print(f"Startmodus: {control_mode}")

mode_button.when_pressed = on_mode_button_press

turn_cw_button.when_pressed = on_turn_cw_press
turn_cw_button.when_released = on_turn_cw_release

turn_ccw_button.when_pressed = on_turn_ccw_press
turn_ccw_button.when_released = on_turn_ccw_release

action_button.when_pressed = on_action_button_press
action_button.when_held = on_action_button_held
action_button.when_released = on_action_button_release

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

if USE_LOCAL_KEYBOARD_INPUT:
    keyboard_thread = threading.Thread(target=local_keyboard_loop, daemon=True)
    keyboard_thread.start()

if USE_MQTT_INPUT:
    client.connect("192.168.209.12", 1883, 60)
    client.loop_forever()  # MQTT-Verbindung aktiv halten
else:
    print("Lokaler Modus aktiv: MQTT-Eingabe ist aus")
    while True:
        time.sleep(1)