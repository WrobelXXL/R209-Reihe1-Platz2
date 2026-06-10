import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import time
import threading
import spidev
import random

# MQTT-Einstellungen
MQTT_BROKER = "192.168.209.12"
MQTT_TOPIC = "R209/Reihe1/Platz2/display"

# Spielfeld
GRID_SIZE = 8
START_INTERVAL = 0.8
MIN_INTERVAL = 0.4
SPEEDUP_PER_FOOD = 0.08
INPUT_DEBOUNCE = 0.12

# Spielstatus
snake = [(4, 4)]
direction = (1, 0)
food = (2, 2)
game_over = False
food_blink_visible = True
tick_interval = START_INTERVAL
last_input_time = 0.0

state_lock = threading.Lock()


def on_connect(client, userdata, flags, rc):
    client.subscribe(MQTT_TOPIC)
    print("Snake verbunden mit Broker")


def blink_led(times=1, duration=0.1):
    """Empfangs-LED blinken."""
    for _ in range(times):
        GPIO.output(27, True)
        time.sleep(duration)
        GPIO.output(27, False)
        time.sleep(duration)


def write_max7219(address, value):
    if spi is None:
        return
    spi.writebytes([address, value])


def init_matrix():
    write_max7219(0x0C, 0x01)
    write_max7219(0x0B, 0x07)
    write_max7219(0x09, 0x00)
    write_max7219(0x0A, 0x0F)
    clear_matrix()


def clear_matrix():
    for row in range(8):
        write_max7219(row + 1, 0x00)


def update_matrix():
    """Zeichnet Schlange und blinkendes Futter."""
    snake_set = set(snake)
    for row in range(8):
        byte_val = 0
        for col in range(8):
            if (col, row) in snake_set:
                byte_val |= (1 << (7 - col))
            elif (col, row) == food and food_blink_visible:
                byte_val |= (1 << (7 - col))
        write_max7219(row + 1, byte_val)


def random_food():
    snake_set = set(snake)
    free = [(x, y) for x in range(GRID_SIZE) for y in range(GRID_SIZE) if (x, y) not in snake_set]
    if not free:
        return None
    return random.choice(free)


def reset_game():
    global snake, direction, food, game_over, tick_interval

    snake = [(4, 4)]
    direction = (1, 0)
    food = random_food() or (2, 2)
    game_over = False
    tick_interval = START_INTERVAL
    print("Neues Spiel gestartet")


def game_over_animation():
    """Kurze Game-Over Animation."""
    for _ in range(2):
        for row in range(8):
            write_max7219(row + 1, 0xFF)
        time.sleep(0.15)
        clear_matrix()
        time.sleep(0.15)


def is_reverse(new_dir):
    return new_dir[0] == -direction[0] and new_dir[1] == -direction[1]


def on_message(client, userdata, msg):
    global direction, last_input_time

    payload = msg.payload.decode("utf-8").strip().lower()

    # Nur Joystick-Richtung auswerten, neutralen Zustand ignorieren.
    if payload == "mitte":
        return

    if payload in {"enter", "reset"}:
        with state_lock:
            reset_game()
            update_matrix()
        blink_led(times=1, duration=0.05)
        return

    command_to_direction = {
        "oben": (0, -1),
        "unten": (0, 1),
        "links": (-1, 0),
        "rechts": (1, 0),
    }

    new_direction = command_to_direction.get(payload)
    if new_direction is None:
        return

    now = time.monotonic()
    if now - last_input_time < INPUT_DEBOUNCE:
        return

    with state_lock:
        if not is_reverse(new_direction):
            direction = new_direction
            last_input_time = now
            print(f"Joystick: {payload}")
            blink_led(times=1, duration=0.03)


def game_loop():
    global game_over, food, tick_interval

    while True:
        time.sleep(tick_interval)

        with state_lock:
            if game_over:
                continue

            head_x, head_y = snake[0]
            dx, dy = direction
            new_head = (head_x + dx, head_y + dy)
            will_eat = (new_head == food)
            body_for_collision = snake if will_eat else snake[:-1]

            # Wand- und Selbstkollision
            if not (0 <= new_head[0] < GRID_SIZE and 0 <= new_head[1] < GRID_SIZE):
                game_over = True
                print("Game Over! Wand getroffen")
            elif new_head in body_for_collision:
                game_over = True
                print("Game Over! Selbst gebissen")
            else:
                snake.insert(0, new_head)
                if will_eat:
                    tick_interval = max(MIN_INTERVAL, tick_interval - SPEEDUP_PER_FOOD)
                    new_food = random_food()
                    if new_food is None:
                        game_over = True
                        print("Sieg! Kein freies Feld mehr")
                    else:
                        food = new_food
                        print(f"Futter gefressen | Speed: {tick_interval:.2f}s")
                else:
                    snake.pop()
                update_matrix()

        if game_over:
            game_over_animation()
            with state_lock:
                reset_game()
                update_matrix()


def blink_food():
    global food_blink_visible

    while True:
        time.sleep(0.3)
        with state_lock:
            if not game_over:
                food_blink_visible = not food_blink_visible
                update_matrix()


# MQTT-Client
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

# GPIO-Setup (nur Empfangs-LED)
GPIO.setmode(GPIO.BCM)
GPIO.setup(27, GPIO.OUT)

# SPI Setup
spi = None
try:
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 1000000
    init_matrix()
    print("MAX7219 Display initialisiert")
except Exception as e:
    print(f"Warnung: MAX7219 Display konnte nicht initialisiert werden: {e}")

with state_lock:
    reset_game()
    update_matrix()

# Hintergrund-Threads
game_thread = threading.Thread(target=game_loop, daemon=True)
game_thread.start()

food_thread = threading.Thread(target=blink_food, daemon=True)
food_thread.start()

# MQTT-Loop
client.connect(MQTT_BROKER, 1883, 60)
client.loop_forever()
