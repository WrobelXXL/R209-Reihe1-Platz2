import paho.mqtt.client as mqtt
import RPi.GPIO as GPIO
import time
import threading
import spidev
import random
import json
import os
from collections import deque
from datetime import datetime
from gpiozero import Button

# MQTT-Einstellungen
MQTT_BROKER = "192.168.209.12"
MQTT_TOPIC = "R209/Reihe1/Platz2/display"

# Spielfeld
GRID_SIZE = 8
START_INTERVAL = 0.8       # Startgeschwindigkeit (langsam)
MIN_INTERVAL = 0.4        # Schnellste Geschwindigkeit
SPEEDUP_PER_FOOD = 0.08    # Pro gefressenem Futter wird's schneller
MARQUEE_ROTATE_90 = True   # True wenn Laufschrift auf deiner Matrix hoch/runter statt rechts/links läuft
MARQUEE_DIGIT_EXTRA_ROTATE = 3  # Zusätzliche 90°-Drehungen nur für Laufschrift-Ziffern

# Spielstatus
snake = [(4, 4)]              # Liste von (x, y)-Tupeln, Kopf an Index 0
direction = (1, 0)            # (dx, dy) - aktuelle Richtung
food = (2, 2)                 # Position vom Futter
game_over = False
score = 0
tick_interval = 0.8           # Aktuelle Geschwindigkeit (wird dynamisch angepasst)
last_input_time = 0            # Letzter Zeitpunkt einer Richtungseingabe (Debouncing)
INPUT_DEBOUNCE = 0.15         # Minimale Zeit zwischen Richtungswechseln (Sekunden)
paused = False                 # Pausierzustand
food_blink_visible = True     # Futter blinkt
press_start_time = 0           # Zeitpunkt des Button-Drucks
showing_highscore = False      # Highscore-Anzeige aktiv
autoplay = False               # KI-Automodus
countdown_active = False       # Countdown läuft gerade
countdown_paused = False       # Countdown ist pausiert
MOVES = [(1, 0), (-1, 0), (0, 1), (0, -1)]

# Highscore-Datei
SCORE_FILE = "/home/pi/Documents/code/src/scores.json"

def load_scores():
    if os.path.exists(SCORE_FILE):
        try:
            with open(SCORE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"highscore": 0, "rounds": []}

def save_score(s):
    data = load_scores()
    if s > data["highscore"]:
        data["highscore"] = s
    data["rounds"].append({"score": s, "date": datetime.now().strftime("%Y-%m-%d %H:%M")})
    with open(SCORE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Score gespeichert: {s} | Highscore: {data['highscore']}")

scores_data = load_scores()
state_lock = threading.Lock()


def on_connect(client, userdata, flags, rc):
    client.subscribe(MQTT_TOPIC)
    print("Snake verbunden mit Broker")


def blink_led(times=1, duration=0.2):
    """Empfangs-LED blinken"""
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
    write_max7219(0x0C, 0x01)  # Normal mode
    write_max7219(0x0B, 0x07)  # Scan limit
    write_max7219(0x09, 0x00)  # No decode
    write_max7219(0x0A, 0x0F)  # Max intensity
    clear_matrix()


def clear_matrix():
    for row in range(8):
        write_max7219(row + 1, 0x00)


def update_matrix():
    """Zeichne Schlange und Futter"""
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
    """Setze Futter auf zufällige freie Position"""
    snake_set = set(snake)
    free = [(x, y) for x in range(GRID_SIZE) for y in range(GRID_SIZE) if (x, y) not in snake_set]
    if not free:
        return None  # Kein Platz mehr - Sieg!
    return random.choice(free)


def reset_game():
    """Spiel zurücksetzen"""
    global snake, direction, food, game_over, score, tick_interval, paused
    snake = [(4, 4)]
    direction = (1, 0)
    food = random_food() or (2, 2)
    game_over = False
    score = 0
    tick_interval = START_INTERVAL
    paused = False
    print("Neues Spiel gestartet")


def game_over_animation():
    """Game Over Animation + Countdown bis Neustart"""
    # 2x kurz blinken
    for _ in range(2):
        for row in range(8):
            write_max7219(row + 1, 0xFF)
        time.sleep(0.2)
        clear_matrix()
        time.sleep(0.2)

    # Countdown 3, 2, 1 anzeigen (kurzer Klick pausiert/fortsetzt)
    run_interruptible_countdown([3, 2, 1])
    clear_matrix()


def run_interruptible_countdown(numbers):
    """Zeigt einen Countdown, der per kurzer Funktionstasten-Betätigung pausierbar ist."""
    global countdown_active, countdown_paused
    countdown_active = True
    countdown_paused = False
    try:
        for num in numbers:
            show_number(num)
            elapsed = 0.0
            while elapsed < 1.0:
                if countdown_paused:
                    time.sleep(0.05)
                    continue
                time.sleep(0.05)
                elapsed += 0.05
    finally:
        countdown_active = False
        countdown_paused = False


# Ziffern als 8x8 Bitmap (jede Zeile ist ein Byte)
DIGITS = {
    0: [
        0b00111100,
        0b01100110,
        0b01100110,
        0b01100110,
        0b01100110,
        0b01100110,
        0b01100110,
        0b00111100,
    ],
    1: [
        0b00011000,
        0b00111000,
        0b01111000,
        0b00011000,
        0b00011000,
        0b00011000,
        0b00011000,
        0b01111110,
    ],
    2: [
        0b00111100,
        0b01100110,
        0b00000110,
        0b00001100,
        0b00011000,
        0b00110000,
        0b01100000,
        0b01111110,
    ],
    3: [
        0b00111100,
        0b01100110,
        0b00000110,
        0b00011100,
        0b00000110,
        0b00000110,
        0b01100110,
        0b00111100,
    ],
    4: [
        0b00001100,
        0b00011100,
        0b00111100,
        0b01101100,
        0b01111110,
        0b00001100,
        0b00001100,
        0b00001100,
    ],
    5: [
        0b01111110,
        0b01100000,
        0b01100000,
        0b01111100,
        0b00000110,
        0b00000110,
        0b01100110,
        0b00111100,
    ],
    6: [
        0b00111100,
        0b01100110,
        0b01100000,
        0b01111100,
        0b01100110,
        0b01100110,
        0b01100110,
        0b00111100,
    ],
    7: [
        0b01111110,
        0b00000110,
        0b00001100,
        0b00011000,
        0b00110000,
        0b00110000,
        0b00110000,
        0b00110000,
    ],
    8: [
        0b00111100,
        0b01100110,
        0b01100110,
        0b00111100,
        0b01100110,
        0b01100110,
        0b01100110,
        0b00111100,
    ],
    9: [
        0b00111100,
        0b01100110,
        0b01100110,
        0b00111110,
        0b00000110,
        0b00000110,
        0b01100110,
        0b00111100,
    ],
}


def show_number(num):
    """Zeigt eine Ziffer auf der Matrix - 90° gedreht"""
    pattern = DIGITS.get(num)
    if pattern is None:
        return
    rotated = [0] * 8
    for r in range(8):
        for c in range(8):
            if pattern[r] & (1 << (7 - c)):
                rotated[c] |= (1 << (7 - (7 - r)))
    for row in range(8):
        write_max7219(row + 1, rotated[row])


def get_rotated_digit_rows(num):
    """Gibt eine Ziffer als 8 rotierte Zeilenbytes zurück."""
    pattern = DIGITS.get(num)
    if pattern is None:
        return [0] * 8
    rotated = [0] * 8
    for r in range(8):
        for c in range(8):
            if pattern[r] & (1 << (7 - c)):
                rotated[c] |= (1 << r)
    return rotated


def rotate_rows_cw(rows):
    """Dreht eine 8x8-Bitmap (als 8 Zeilenbytes) um 90 Grad im Uhrzeigersinn."""
    out = [0] * 8
    for r in range(8):
        for c in range(8):
            if rows[r] & (1 << (7 - c)):
                out[c] |= (1 << r)
    return out


def get_digit_columns(num):
    """Gibt eine Ziffer als 8 Display-Spalten zurück."""
    rows = get_rotated_digit_rows(num)
    for _ in range(MARQUEE_DIGIT_EXTRA_ROTATE):
        rows = rotate_rows_cw(rows)
    cols = []
    for c in range(8):
        col_byte = 0
        for r in range(8):
            if rows[r] & (1 << (7 - c)):
                col_byte |= (1 << (7 - r))
        cols.append(col_byte)
    return cols


def rotate_frame_cw(frame):
    """Dreht ein 8x8-Pixel-Frame um 90 Grad im Uhrzeigersinn."""
    return [[frame[7 - r][c] for r in range(8)] for c in range(8)]


def draw_scrolling_window(columns, offset):
    """Zeichnet ein 8x8 Fenster aus einer breiten Spaltenliste."""
    window = columns[offset:offset + 8]
    if len(window) < 8:
        window += [0] * (8 - len(window))

    # Frame als Pixelmatrix aufbauen (Zeilen x Spalten)
    frame = [[0] * 8 for _ in range(8)]
    for col in range(8):
        col_byte = window[col]
        for row in range(8):
            frame[row][col] = 1 if (col_byte & (1 << (7 - row))) else 0

    # Bei gedrehter Matrix Richtung/Orientierung kompensieren
    if MARQUEE_ROTATE_90:
        frame = rotate_frame_cw(frame)

    for row in range(8):
        row_val = 0
        for col in range(8):
            if frame[row][col]:
                row_val |= (1 << (7 - col))
        write_max7219(row + 1, row_val)


def show_highscore_display():
    """Zeigt den Highscore als Laufschrift dauerhaft an bis Button gedrückt wird."""
    global showing_highscore
    showing_highscore = True
    data = load_scores()
    hs = data["highscore"]
    print(f"Highscore Laufschrift: {hs}")
    digits = [int(d) for d in str(hs)]

    # Breite Spaltenliste bauen: links/rechts Leerraum, zwischen Ziffern 1 Leer-Spalte
    columns = [0] * 8
    for d in digits:
        columns.extend(get_digit_columns(d))
        columns.append(0)
    columns.extend([0] * 8)

    width = len(columns)

    # Endlos scrollen bis Button-Druck
    while showing_highscore:
        for offset in range(0, width - 7):
            if not showing_highscore:
                break
            draw_scrolling_window(columns, offset)
            # ca. 0.1s pro Schritt (flüssige Laufschrift), mit schneller Abbruchprüfung
            for _ in range(2):
                if not showing_highscore:
                    break
                time.sleep(0.05)
    clear_matrix()
    with state_lock:
        update_matrix()


def in_bounds(pos):
    return 0 <= pos[0] < GRID_SIZE and 0 <= pos[1] < GRID_SIZE


def neighbors(pos):
    for dx, dy in MOVES:
        yield (pos[0] + dx, pos[1] + dy), (dx, dy)


def find_path(start, target, blocked):
    """BFS-Pfad als Liste von Moves [(dx,dy), ...]."""
    if start == target:
        return []

    queue = deque([start])
    prev = {start: None}
    prev_move = {}

    while queue:
        cur = queue.popleft()
        for nxt, move in neighbors(cur):
            if not in_bounds(nxt):
                continue
            if nxt != target and nxt in blocked:
                continue
            if nxt in prev:
                continue
            prev[nxt] = cur
            prev_move[nxt] = move
            if nxt == target:
                path = []
                node = nxt
                while prev[node] is not None:
                    path.append(prev_move[node])
                    node = prev[node]
                path.reverse()
                return path
            queue.append(nxt)
    return None


def simulate_move(sim_snake, move, sim_food):
    """Simuliert einen Move. Rückgabe: (neue_snake, ate_food) oder (None, False) bei Kollision."""
    head_x, head_y = sim_snake[0]
    dx, dy = move
    new_head = (head_x + dx, head_y + dy)

    if not in_bounds(new_head):
        return None, False

    ate_food = (new_head == sim_food)
    body = sim_snake if ate_food else sim_snake[:-1]
    if new_head in body:
        return None, False

    new_snake = [new_head] + sim_snake
    if not ate_food:
        new_snake.pop()
    return new_snake, ate_food


def simulate_path(sim_snake, path, sim_food):
    """Simuliert eine komplette Move-Liste."""
    cur = list(sim_snake)
    ate = False
    for move in path:
        cur, step_ate = simulate_move(cur, move, sim_food)
        if cur is None:
            return None, False
        ate = ate or step_ate
    return cur, ate


def reachable_cells_count(start, blocked):
    """Anzahl erreichbarer Felder ab start (Flood Fill)."""
    if not in_bounds(start) or start in blocked:
        return 0
    queue = deque([start])
    seen = {start}
    while queue:
        cur = queue.popleft()
        for nxt, _ in neighbors(cur):
            if not in_bounds(nxt) or nxt in blocked or nxt in seen:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return len(seen)


def bfs_next_direction():
    """Sichere KI: Futter nur nehmen, wenn danach Fluchtweg bleibt."""
    head = snake[0]

    # 1) Kürzester Weg zum Futter (Tail-Feld darf betreten werden, weil es meist frei wird)
    blocked_to_food = set(snake[:-1])
    path_to_food = find_path(head, food, blocked_to_food)

    if path_to_food:
        sim_snake, _ = simulate_path(snake, path_to_food, food)
        if sim_snake:
            # Nach dem Essen muss ein Weg zum eigenen Tail bestehen (Anti-Sackgasse)
            sim_head = sim_snake[0]
            sim_tail = sim_snake[-1]
            blocked_after_food = set(sim_snake[1:-1])
            path_to_tail = find_path(sim_head, sim_tail, blocked_after_food)
            if path_to_tail is not None:
                return path_to_food[0]

    # 2) Kein sicherer Futterweg: versuche dem Tail zu folgen (Survival-Strategie)
    tail = snake[-1]
    blocked_to_tail = set(snake[1:-1])
    path_to_tail = find_path(head, tail, blocked_to_tail)
    if path_to_tail:
        return path_to_tail[0]

    # 3) Fallback: sichersten Zug nach erreichbarem Raum wählen
    best_move = None
    best_space = -1
    for move in MOVES:
        sim_snake, _ = simulate_move(snake, move, food)
        if sim_snake is None:
            continue
        sim_head = sim_snake[0]
        blocked = set(sim_snake[1:])
        space = reachable_cells_count(sim_head, blocked)
        if space > best_space:
            best_space = space
            best_move = move

    return best_move if best_move is not None else direction


def game_loop():
    """Bewegt die Schlange in regelmäßigen Abständen"""
    global snake, food, game_over, score, tick_interval, paused, direction
    while True:
        time.sleep(tick_interval)
        with state_lock:
            if game_over or paused or showing_highscore:
                continue

            # Autoplay: KI berechnet beste Richtung
            if autoplay:
                direction = bfs_next_direction()

            head_x, head_y = snake[0]
            dx, dy = direction
            new_head = (head_x + dx, head_y + dy)
            will_eat = (new_head == food)
            body_for_collision = snake if will_eat else snake[:-1]

            # Wand-Kollision
            if not (0 <= new_head[0] < GRID_SIZE and 0 <= new_head[1] < GRID_SIZE):
                game_over = True
                save_score(score)
                print(f"Game Over! Wand getroffen. Score: {score}")
                update_matrix_unsafe = True
            # Selbst-Kollision
            elif new_head in body_for_collision:
                game_over = True
                save_score(score)
                print(f"Game Over! Selbst gebissen. Score: {score}")
                update_matrix_unsafe = True
            else:
                snake.insert(0, new_head)
                if will_eat:
                    score += 1
                    # Geschwindigkeit erhöhen (Intervall verringern)
                    tick_interval = max(MIN_INTERVAL, tick_interval - SPEEDUP_PER_FOOD)
                    print(f"Futter gefressen! Score: {score} | Speed: {tick_interval:.2f}s")
                    new_food = random_food()
                    if new_food is None:
                        game_over = True
                        print(f"Du hast gewonnen! Score: {score}")
                    else:
                        food = new_food
                else:
                    snake.pop()
                update_matrix()

        if game_over:
            if not autoplay:
                game_over_animation()
            else:
                time.sleep(0.5)
            with state_lock:
                reset_game()
                update_matrix()


def blink_food():
    """Lässt das Futter blinken"""
    global food_blink_visible
    while True:
        time.sleep(0.3)
        with state_lock:
            if not game_over and not showing_highscore:
                food_blink_visible = not food_blink_visible
                update_matrix()


def on_message(client, userdata, msg):
    global direction, last_input_time
    payload = msg.payload.decode("utf-8").lower()
    
    # Ignoriere "mitte" (neutraler Befehl)
    if payload == "mitte":
        return
    
    # Debouncing: Nur Richtungswechsel verarbeiten wenn genug Zeit vergangen ist
    current_time = time.time()
    if current_time - last_input_time < INPUT_DEBOUNCE:
        return
    
    print("Empfangen:", payload)
    blink_led(times=1, duration=0.1)
    last_input_time = current_time

    with state_lock:
        if payload == "oben":
            if direction != (-1, 0):  # nicht 180° umkehren
                direction = (1, 0)
        elif payload == "unten":
            if direction != (1, 0):
                direction = (-1, 0)
        elif payload == "links":
            if direction != (0, 1):
                direction = (0, -1)
        elif payload == "rechts":
            if direction != (0, -1):
                direction = (0, 1)
        elif payload == "reset" or payload == "enter":
            reset_game()
            update_matrix()
        elif payload == "flush":
            print("Flush: Warteschlange geleert")


# MQTT-Client
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

# GPIO-Setup
GPIO.setmode(GPIO.BCM)
GPIO.setup(27, GPIO.OUT)  # Empfangen LED

# Button-Setup
button_function = Button(26)
button_cw = Button(20)
button_ccw = Button(21)
press_start_time = 0


def rotate_clockwise():
    global direction
    with state_lock:
        if not paused and not autoplay and not showing_highscore:
            dx, dy = direction
            direction = (-dy, dx)
            print("GPIO20: Richtung im Uhrzeigersinn")


def rotate_counterclockwise():
    global direction
    with state_lock:
        if not paused and not autoplay and not showing_highscore:
            dx, dy = direction
            direction = (dy, -dx)
            print("GPIO21: Richtung gegen Uhrzeigersinn")


def on_button_press():
    global press_start_time
    press_start_time = time.time()


def on_button_release():
    global paused, showing_highscore, autoplay, countdown_paused
    duration = time.time() - press_start_time
    print(f"Button gehalten: {duration:.2f}s")

    # Während Countdown: kurzer Klick pausiert/fortsetzt den Countdown, andere Aktionen ignorieren
    if countdown_active:
        if duration < 2:
            countdown_paused = not countdown_paused
            print("COUNTDOWN PAUSIERT" if countdown_paused else "COUNTDOWN WEITER")
        return

    # Highscore-Anzeige: immer sofort beenden
    if showing_highscore:
        showing_highscore = False
        return

    # Autoplay: sofort beenden (ohne Countdown)
    if autoplay:
        with state_lock:
            autoplay = False
            paused = False
        print("Autoplay beendet - manuelle Steuerung aktiv")
        return

    if duration >= 4:
        # 4+ Sekunden = Autoplay starten
        print("AUTOPLAY - KI übernimmt")
        autoplay = True
        paused = False
        with state_lock:
            reset_game()
            update_matrix()
    elif duration >= 2:
        # 2-4 Sekunden = Highscore als Laufschrift anzeigen
        print("LANGER DRUCK - Highscore Laufschrift")
        threading.Thread(target=show_highscore_display, daemon=True).start()
    else:
        # Kurz drücken = Pause/Resume
        with state_lock:
            paused = not paused
            print("PAUSIERT" if paused else "WEITERGESPIELT")


button_function.when_pressed = on_button_press
button_function.when_released = on_button_release
button_cw.when_pressed = rotate_clockwise
button_ccw.when_pressed = rotate_counterclockwise

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

# Spiel initialisieren
food = random_food() or (2, 2)
paused = True  # Spiel wartet auf Countdown
with state_lock:
    update_matrix()

# Countdown 3, 2, 1 vor Spielstart
run_interruptible_countdown([3, 2, 1])
clear_matrix()
paused = False  # Spiel startet
with state_lock:
    update_matrix()

# Game-Loop in Thread
game_thread = threading.Thread(target=game_loop, daemon=True)
game_thread.start()

# Futter-Blink-Thread
food_thread = threading.Thread(target=blink_food, daemon=True)
food_thread.start()

# MQTT-Loop
client.connect(MQTT_BROKER, 1883, 60)
client.loop_forever()
