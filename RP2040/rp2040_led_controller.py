# ╔══════════════════════════════════════════════════════════════════╗
# ║   LED RGB Controller — Firmware RP2040 v6                        ║
# ║   MicroPython v1.28 — Protocole texte hex                        ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Protocole : chaque commande est envoyée en ASCII hex            ║
# ║  Format : "XX YYYYYY\n"  (cmd hex + data hex + newline)          ║
# ║  Exemple couleur rouge : "01 FF0000\n"                           ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  CÂBLAGE :                                                       ║
# ║   Data ruban  → GPIO 0  (broche 1)                               ║
# ║   VCC ruban   → alim 5V externe                                  ║
# ║   GND ruban   → GND Pico + GND alim externe                      ║
# ║   Résistance 300–470Ω sur Data                                   ║
# ╚══════════════════════════════════════════════════════════════════╝

import machine, neopixel, time, random, math, sys

# ── Configuration ────────────────────────────────────────────────────
LED_PIN   = 29
MAX_LEDS  = 300
DEFAULT_N = 60

# ── NeoPixel ─────────────────────────────────────────────────────────
np = neopixel.NeoPixel(machine.Pin(LED_PIN, machine.Pin.OUT), MAX_LEDS)
for i in range(MAX_LEDS):
    np[i] = (0, 0, 0)
np.write()

# ── État ─────────────────────────────────────────────────────────────
num_leds   = DEFAULT_N
col1       = (255, 0, 0)
col2       = (0, 0, 255)
brightness = 200
effect_id  = 0
speed      = 128
power_on   = True

# Tableau de chaleur pour l'effet Feu
heat = [0] * MAX_LEDS

# ── Couleurs ─────────────────────────────────────────────────────────
def bri(r, g, b, lum=None):
    f = (brightness if lum is None else lum) / 255.0
    return (int(r*f), int(g*f), int(b*f))

def hsv(h, s, v):
    h = int(h) % 360
    i = h // 60; f = (h % 60) / 60.0
    p = v*(1-s); q = v*(1-s*f); t = v*(1-s*(1-f))
    rv,gv,bv = [(v,t,p),(q,v,p),(p,v,t),(p,q,v),(t,p,v),(v,p,q)][i%6]
    return (int(rv*255), int(gv*255), int(bv*255))

def blend_c(c1, c2, t):
    f = t/255.0
    return (int(c1[0]*(1-f)+c2[0]*f),
            int(c1[1]*(1-f)+c2[1]*f),
            int(c1[2]*(1-f)+c2[2]*f))

def fill_all(color):
    c = bri(*color)
    for i in range(num_leds): np[i] = c

def fade_all(amount):
    for i in range(num_leds):
        r,g,b = np[i]
        np[i] = (max(0,r-amount), max(0,g-amount), max(0,b-amount))

# ── Décodage hex ─────────────────────────────────────────────────────
def from_hex(s):
    """Convertit une chaîne hex en liste d'entiers."""
    s = s.strip().replace(" ", "")
    out = []
    for i in range(0, len(s)-1, 2):
        try:
            out.append(int(s[i:i+2], 16))
        except:
            pass
    return out

# ── Traitement commandes ──────────────────────────────────────────────
def process_line(line):
    global num_leds, col1, col2, brightness, effect_id, speed, power_on
    line = line.strip()
    if not line: return
    parts = line.split()
    if not parts: return
    try:
        cmd = int(parts[0], 16)
    except:
        return
    data = []
    if len(parts) > 1:
        data = from_hex(parts[1])

    if cmd == 0x01:
        if len(data) >= 3: col1 = (data[0], data[1], data[2])
    elif cmd == 0x02:
        if data: brightness = data[0]
    elif cmd == 0x03:
        if len(data) >= 2:
            num_leds = min((data[0]<<8)|data[1], MAX_LEDS)
            heat = [0] * MAX_LEDS
    elif cmd == 0x04:
        effect_id = data[0] if data else 0
        if len(data) >= 2: speed = data[1]
    elif cmd == 0x05:
        if len(data) >= 3: col2 = (data[0], data[1], data[2])
    elif cmd == 0x06:
        power_on = (data[0] == 1) if data else True
        if not power_on:
            for i in range(MAX_LEDS): np[i] = (0,0,0)
            np.write()
    elif cmd == 0x07:
        if len(data) < 4: return
        s = (data[0]<<8)|data[1]
        n = (data[2]<<8)|data[3]
        n = min(n, (len(data)-4)//3)
        for i in range(n):
            idx = s+i
            if idx >= MAX_LEDS: break
            np[idx] = bri(data[4+i*3], data[4+i*3+1], data[4+i*3+2])
        np.write()
    elif cmd == 0xF0:
        sys.stdout.write("F1\n")
    elif cmd == 0xFF:
        machine.reset()

# ── Lecture ligne par ligne ───────────────────────────────────────────
_line_buf = ""

def read_usb():
    global _line_buf
    try:
        # readline non-bloquant : lit ce qui est dispo
        import uselect
        poll = uselect.poll()
        poll.register(sys.stdin, uselect.POLLIN)
        while poll.poll(0):
            c = sys.stdin.read(1)
            if c:
                if c == '\n':
                    process_line(_line_buf)
                    _line_buf = ""
                else:
                    _line_buf += c
                    if len(_line_buf) > 256:
                        _line_buf = ""  # protection overflow
    except Exception as e:
        pass

# ── Effets ────────────────────────────────────────────────────────────
_hue = 0
_t   = 0

def run_effect():
    global _hue, _t
    if not power_on: return
    delay = max(5, (255-speed)//3)
    e = effect_id

    if e == 0:    fill_all(col1)
    elif e == 1:
        for i in range(num_leds):
            np[i] = bri(*blend_c(col1, col2, int(i*255/max(num_leds-1,1))))
    elif e == 2:
        for i in range(num_leds):
            np[i] = bri(*hsv((_hue+i*360//max(num_leds,1))%360, 1.0, 1.0))
        _hue = (_hue + max(1,speed//32)) % 360
    elif e == 3:
        lum = int((math.sin(_t*speed/800.0)+1)/2*255)
        c = bri(*col1, lum)
        for i in range(num_leds): np[i] = c
    elif e == 4:
        on = ((_t//max(1,(256-speed)//4))%2)==0
        c = bri(*col1) if on else (0,0,0)
        for i in range(num_leds): np[i] = c
    elif e == 5:
        # Algorithme heat map : refroidissement + propagation + sources
        cooling = max(1, (255 - speed) // 8)
        # Refroidissement aléatoire
        for k in range(num_leds):
            cool = random.randint(0, cooling)
            heat[k] = max(0, heat[k] - cool)
        # Propagation chaleur (convection vers le haut/droite)
        for k in range(num_leds-1, 1, -1):
            heat[k] = (heat[k-1] * 2 + heat[k-2] + heat[k] * 5) // 8
        # Nouvelles étincelles à la source
        if random.randint(0, 255) < 160 + speed // 3:
            spark = random.randint(0, min(2, num_leds-1))
            heat[spark] = min(255, heat[spark] + random.randint(100, 255))
        # Conversion chaleur → couleur feu
        for k in range(num_leds):
            h = heat[k]
            if h < 85:
                np[k] = bri(h * 3, 0, 0)
            elif h < 170:
                np[k] = bri(255, (h - 85) * 2, 0)
            else:
                np[k] = bri(255, 160, (h - 170) * 3)
    elif e == 6:
        for i in range(num_leds):
            p = math.sin(i*0.5 - _t*speed/3000.0)
            np[i] = bri(*blend_c(col1, col2, int((p+1)/2*255)))
    elif e == 7:
        fade_all(20)
        np[int(_t*speed/500.0)%max(num_leds,1)] = bri(*col1)
    elif e == 8:
        fade_all(40)
        for i in range(num_leds):
            if random.randint(0,255)<max(1,speed//4): np[i]=bri(*col1)
    elif e == 9:
        on = ((_t//max(1,(256-speed)//4))%2)==0
        for i in range(num_leds):
            np[i] = bri(0,0,255) if on else bri(255,0,0)
    elif e == 10: pass
    elif e == 11:
        fade_all(max(1,speed//128))
        for _ in range(max(1,num_leds*speed//12000)):
            np[random.randint(0,max(num_leds-1,0))]=hsv(random.randint(0,360),1.0,1.0)

    np.write()
    _t += 1
    time.sleep_ms(delay)

# ── Démarrage ─────────────────────────────────────────────────────────
for _ in range(3):
    for i in range(min(3,MAX_LEDS)): np[i]=(0,80,0)
    np.write(); time.sleep_ms(200)
    for i in range(min(3,MAX_LEDS)): np[i]=(0,0,0)
    np.write(); time.sleep_ms(200)

fill_all(col1); np.write()
sys.stdout.write("READY\n")

# ── Boucle principale ─────────────────────────────────────────────────
while True:
    try:
        read_usb()
        run_effect()
    except Exception as e:
        time.sleep_ms(50)