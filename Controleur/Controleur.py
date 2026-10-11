#!/usr/bin/env python3
"""
RGB LED Strip Controller — ESP32-S3 via Bluetooth
Interface graphique tkinter + communication BLE/Serial
"""

import tkinter as tk
from tkinter import ttk, messagebox, colorchooser
import threading
import time
import json
import math
import asyncio
import struct
from datetime import datetime

# Tentative d'import des libs Bluetooth
try:
    import bleak
    from bleak import BleakClient, BleakScanner
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# ── Libs mode Immersion ───────────────────────────────────────────────────────
try:
    from PIL import ImageGrab, Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False


# ── Constantes BLE ──────────────────────────────────────────────────────────
# UUID du service NimBLE / BLE UART (Nordic UART Service)
UART_SERVICE_UUID    = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_CHAR_UUID    = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # Write
UART_TX_CHAR_UUID    = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # Notify


# ── Effets disponibles ────────────────────────────────────────────────────────
EFFECTS = {
    "Statique":         0,
    "Dégradé":          1,
    "Arc-en-ciel":      2,
    "Respiration":      3,
    "Stroboscope":      4,
    "Feu":              5,
    "Vague":            6,
    "Comète":           7,
    "Twinkle":          8,
    "Police / Flash":   9,
    "✏ Personnalisé":  10,
    "🌌 Galaxie":       11,
}


class BluetoothManager:
    """Gère la connexion vers ESP32-S3 (BLE/WiFi/Serial) ou RP2040 (USB-CDC)."""

    def __init__(self, log_cb):
        self.log = log_cb
        self.connected = False
        self.client = None
        self.serial_port = None
        self.mode = None        # "ble" | "serial" | "wifi" | "rp2040"
        self._loop = None
        self._ble_thread = None

    # ── BLE ──────────────────────────────────────────────────────────────────
    def scan_ble(self, result_cb):
        """Lance un scan BLE dans un thread séparé."""
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            devices = loop.run_until_complete(self._async_scan())
            loop.close()
            result_cb(devices)
        threading.Thread(target=_run, daemon=True).start()

    async def _async_scan(self):
        self.log("🔍 Scan BLE en cours…")
        try:
            devices = await BleakScanner.discover(timeout=5.0)
            return [(d.name or "Inconnu", d.address) for d in devices]
        except Exception as e:
            self.log(f"❌ Erreur scan : {e}")
            return []

    def connect_ble(self, address, on_connect, on_disconnect):
        """Connexion BLE dans son propre thread/loop."""
        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(
                self._async_connect_ble(address, on_connect, on_disconnect)
            )
        self._ble_thread = threading.Thread(target=_run, daemon=True)
        self._ble_thread.start()

    async def _async_connect_ble(self, address, on_connect, on_disconnect):
        try:
            self.log(f"🔗 Connexion BLE → {address}…")
            self.client = BleakClient(address, disconnected_callback=lambda _: on_disconnect())
            await self.client.connect()
            self.connected = True
            self.mode = "ble"
            self.log("✅ Connecté en BLE !")
            on_connect()
            # Garder la loop active
            while self.connected:
                await asyncio.sleep(0.1)
        except Exception as e:
            self.log(f"❌ Connexion BLE échouée : {e}")
            self.connected = False

    def send_ble(self, data: bytes):
        if self._loop and self.client and self.connected:
            asyncio.run_coroutine_threadsafe(
                self._async_write(data), self._loop
            )

    async def _async_write(self, data: bytes):
        try:
            await self.client.write_gatt_char(UART_RX_CHAR_UUID, data, response=False)
        except Exception as e:
            self.log(f"⚠️ Erreur envoi BLE : {e}")

    def disconnect_ble(self):
        async def _dc():
            if self.client:
                await self.client.disconnect()
        if self._loop:
            asyncio.run_coroutine_threadsafe(_dc(), self._loop)
        self.connected = False

    # ── Serial (SPP / USB-CDC fallback) ──────────────────────────────────────
    def connect_serial(self, port, baud=115200):
        try:
            self.serial_port = serial.Serial(port, baud, timeout=1)
            self.connected = True
            self.mode = "serial"
            self.log(f"✅ Connecté en Serial sur {port}")
            return True
        except Exception as e:
            self.log(f"❌ Erreur Serial : {e}")
            return False

    def send_serial(self, data: bytes):
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write(data)
            except Exception as e:
                self.log(f"⚠️ Erreur envoi Serial : {e}")

    def disconnect_serial(self):
        if self.serial_port:
            self.serial_port.close()
        self.connected = False

    # ── WiFi TCP Socket ───────────────────────────────────────────────────────
    def connect_wifi(self, host: str, port: int, on_connect, on_disconnect):
        """Connexion TCP vers l'ESP32 en WiFi dans un thread séparé."""
        def _run():
            import socket
            try:
                self.log(f"🔗 Connexion WiFi → {host}:{port}…")
                self._wifi_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._wifi_socket.settimeout(5)
                self._wifi_socket.connect((host, port))
                self._wifi_socket.settimeout(None)
                self.connected = True
                self.mode = "wifi"
                self.log(f"✅ Connecté en WiFi à {host}:{port}")
                on_connect()
                # Garder le thread vivant pour détecter une déco
                try:
                    while self.connected:
                        time.sleep(0.5)
                        # Ping keep-alive silencieux
                except Exception:
                    pass
            except Exception as e:
                self.log(f"❌ Connexion WiFi échouée : {e}")
                self.connected = False
                on_disconnect()
        threading.Thread(target=_run, daemon=True).start()

    def send_wifi(self, data: bytes):
        if hasattr(self, "_wifi_socket") and self._wifi_socket:
            try:
                self._wifi_socket.sendall(data)
            except Exception as e:
                self.log(f"⚠️ Erreur envoi WiFi : {e}")
                self.connected = False

    def disconnect_wifi(self):
        if hasattr(self, "_wifi_socket") and self._wifi_socket:
            try:
                self._wifi_socket.close()
            except Exception:
                pass
            self._wifi_socket = None
        self.connected = False

    # ── RP2040 USB-CDC ────────────────────────────────────────────────────────
    def scan_rp2040(self):
        """Retourne les ports série qui ressemblent à un RP2040."""
        if not SERIAL_AVAILABLE:
            return []
        import serial.tools.list_ports
        found = []
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").lower()
            vid  = p.vid or 0
            # RP2040 VID Raspberry Pi = 0x2E8A, ou description contient "pico"
            if vid == 0x2E8A or "pico" in desc or "rp2040" in desc or "raspberry" in desc:
                found.append((f"RP2040 — {p.device}", p.device))
            else:
                found.append((f"{p.description} — {p.device}", p.device))
        return found

    def connect_rp2040(self, port, baud=115200, on_connect=None, on_disconnect=None):
        try:
            self.serial_port = serial.Serial(
                port, baud,
                timeout=0,           # non-bloquant en lecture
                write_timeout=1,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            # Vider les buffers
            time.sleep(0.1)
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()
            self.connected = True
            self.mode = "rp2040"
            self.log(f"✅ RP2040 connecté sur {port} ({baud} baud)")
            if on_connect:
                on_connect()
            return True
        except Exception as e:
            self.log(f"❌ Erreur connexion RP2040 : {e}")
            return False

    def disconnect_rp2040(self):
        if self.serial_port:
            try:
                self.serial_port.close()
            except Exception:
                pass
        self.connected = False

    # ── Interface unifiée ─────────────────────────────────────────────────────
    def _to_hex_line(self, data: bytes) -> bytes:
        """
        Convertit un paquet binaire [CMD DATA... 0xFF] en ligne hex ASCII.
        Format envoyé : "CMD DATAHEX\n"
        Ex: b'\x01\xFF\x00\x00\xFF' → b'01 FF0000\n'
        """
        if len(data) < 2:
            return b''
        cmd = data[0]
        # data sans le 0xFF final
        payload = data[1:-1] if data[-1] == 0xFF else data[1:]
        hex_payload = ''.join(f'{b:02X}' for b in payload)
        if hex_payload:
            line = f'{cmd:02X} {hex_payload}\n'
        else:
            line = f'{cmd:02X}\n'
        return line.encode('ascii')

    def send(self, data: bytes):
        if not self.connected:
            return
        if self.mode == "ble":
            self.send_ble(data)
        elif self.mode == "rp2040":
            # Protocole hex texte pour RP2040
            line = self._to_hex_line(data)
            if line and self.serial_port and self.serial_port.is_open:
                try:
                    self.serial_port.write(line)
                    self.serial_port.flush()
                except Exception as e:
                    self.log(f"⚠️ Erreur envoi RP2040 : {e}")
        elif self.mode == "serial":
            self.send_serial(data)
        elif self.mode == "wifi":
            self.send_wifi(data)

    def disconnect(self):
        if self.mode == "ble":
            self.disconnect_ble()
        elif self.mode in ("serial", "rp2040"):
            self.disconnect_serial()
        elif self.mode == "wifi":
            self.disconnect_wifi()
        self.connected = False
        self.mode = None


class CommandBuilder:
    """
    Protocole binaire léger vers l'ESP32.
    Format : [CMD:1] [DATA…] [0xFF]
    Codes :
      0x01 → Couleur statique  : R G B
      0x02 → Luminosité        : L (0-255)
      0x03 → Nombre de LEDs    : N_H N_L
      0x04 → Effet             : ID SPEED
      0x05 → Couleur 2         : R G B
      0x06 → Power             : 0/1
    """
    END = b'\xFF'

    @staticmethod
    def color(r, g, b):
        return bytes([0x01, r, g, b]) + CommandBuilder.END

    @staticmethod
    def brightness(val):
        return bytes([0x02, max(0, min(255, val))]) + CommandBuilder.END

    @staticmethod
    def led_count(n):
        return bytes([0x03, (n >> 8) & 0xFF, n & 0xFF]) + CommandBuilder.END

    @staticmethod
    def effect(effect_id, speed):
        return bytes([0x04, effect_id, max(0, min(255, speed))]) + CommandBuilder.END

    @staticmethod
    def color2(r, g, b):
        return bytes([0x05, r, g, b]) + CommandBuilder.END

    @staticmethod
    def power(on: bool):
        return bytes([0x06, 0x01 if on else 0x00]) + CommandBuilder.END

    @staticmethod
    def custom_leds(led_colors: list):
        """
        Envoie les couleurs individuelles de chaque LED.
        Format : [0x07] [N_H] [N_L] [R0 G0 B0] [R1 G1 B1] … [0xFF]
        Envoyé en paquets de 18 LEDs max (BLE MTU ~512 octets)
        """
        packets = []
        chunk = 18
        n = len(led_colors)
        for start in range(0, n, chunk):
            end = min(start + chunk, n)
            sub = led_colors[start:end]
            data = bytes([0x07,
                          (start >> 8) & 0xFF, start & 0xFF,
                          (len(sub) >> 8) & 0xFF, len(sub) & 0xFF])
            for r, g, b in sub:
                data += bytes([r, g, b])
            data += CommandBuilder.END
            packets.append(data)
        return packets


# ─────────────────────────────────────────────────────────────────────────────
class LEDControllerApp(tk.Tk):
    # Couleurs mesurées sur la capture d'écran
    BG      = "#16161A"   # fond fenêtre très sombre
    # ── Thèmes ────────────────────────────────────────────────────────────────
    THEMES = {
        "dark": {
            "BG":    "#16161A",
            "CARD":  "#1F1F25",
            "CARD2": "#2A2A32",
            "CARD3": "#323239",
            "TEXT":  "#FFFFFF",
            "MUTED": "#6B7280",
            "MUTED2":"#9CA3AF",
        },
        "light": {
            "BG":    "#F0F0F5",
            "CARD":  "#FFFFFF",
            "CARD2": "#E8E8EF",
            "CARD3": "#D0D0DA",
            "TEXT":  "#111118",
            "MUTED": "#6B7280",
            "MUTED2":"#4B5563",
        },
    }
    ACCENT  = "#3B82F6"
    SUCCESS = "#22C55E"
    DANGER  = "#EF4444"
    WARN    = "#F59E0B"
    RADIUS  = 10

    def _apply_theme(self, name):
        t = self.THEMES[name]
        self.BG    = t["BG"]
        self.CARD  = t["CARD"]
        self.CARD2 = t["CARD2"]
        self.CARD3 = t["CARD3"]
        self.TEXT  = t["TEXT"]
        self.MUTED = t["MUTED"]
        self.MUTED2= t["MUTED2"]

    # Alias PALETTE pour compatibilité avec le reste du code
    @property
    def PALETTE(self):
        return {
            "bg": self.BG, "panel": self.BG, "card": self.CARD,
            "card2": self.CARD2, "accent": self.ACCENT, "accent2": self.ACCENT,
            "text": self.TEXT, "muted": self.MUTED,
            "success": self.SUCCESS, "danger": self.DANGER, "warn": self.WARN,
        }

    def __init__(self):
        super().__init__()
        self.title("LED RGB Controller — ESP32-S3 / RP2040")
        self.geometry("1360x1020")
        self.minsize(1100, 900)
        self.configure(bg=self.BG)
        self.resizable(True, True)

        self._current_theme = "dark"
        self._apply_theme("dark")

        self.bt = BluetoothManager(self.log)
        self.cmd = CommandBuilder()

        # State
        self.color1 = "#FF0000"
        self.color2 = "#0000FF"
        self.power_on    = tk.BooleanVar(value=False)
        self.brightness  = tk.IntVar(value=191)
        self.led_count   = tk.IntVar(value=60)
        self.effect_var  = tk.StringVar(value="Statique")
        self.speed_var   = tk.IntVar(value=128)
        self.scan_results = []
        self.custom_led_colors = []

        self.rgb_r = tk.IntVar(value=255)
        self.rgb_g = tk.IntVar(value=0)
        self.rgb_b = tk.IntVar(value=0)
        self.hex_var = tk.StringVar(value="#FF0000")
        self._updating_color = False
        self._hsv_h = 0.0; self._hsv_s = 1.0; self._hsv_v = 1.0

        self._immersion_active = False
        self._immersion_thread = None
        self._immersion_color  = (255, 0, 0)
        self._immersion_bri    = 200
        self._audio_level      = 0.0
        self._audio_buffer     = []
        self._audio_enabled    = tk.BooleanVar(value=True)

        self._ambilight_active = False
        self._ambilight_thread = None
        self._ambilight_colors = []   # liste (r,g,b) par zone

        self._build_ui()
        self._start_preview_loop()

    # ─────────────────────────────────────────────────────────────────────────
    # TTK style
    # ─────────────────────────────────────────────────────────────────────────
    def _style_ttk(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        # Slider : piste bleue, thumb rond blanc
        s.configure("W.Horizontal.TScale",
                    background=self.CARD,
                    troughcolor=self.ACCENT,
                    sliderlength=20, sliderrelief="flat",
                    troughrelief="flat", borderwidth=0)
        s.map("W.Horizontal.TScale", background=[("active", self.CARD)])
        # Combobox
        s.configure("TCombobox",
                    fieldbackground=self.CARD2,
                    background=self.CARD2,
                    foreground=self.TEXT,
                    selectbackground=self.ACCENT,
                    borderwidth=0, arrowcolor=self.MUTED2,
                    padding=[8, 6])
        s.map("TCombobox",
              fieldbackground=[("readonly", self.CARD2)],
              foreground=[("readonly", self.TEXT)])
        # Notebook
        s.configure("TNotebook", background=self.CARD, borderwidth=0)
        s.configure("TNotebook.Tab", background=self.CARD2,
                    foreground=self.MUTED, padding=[14, 7], borderwidth=0)
        s.map("TNotebook.Tab",
              background=[("selected", self.ACCENT)],
              foreground=[("selected", self.TEXT)])

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _lighten(hx, f=1.15):
        hx = hx.lstrip("#")
        r, g, b = [min(255, int(int(hx[i:i+2],16)*f)) for i in (0,2,4)]
        return f"#{r:02X}{g:02X}{b:02X}"

    def _rounded_frame(self, parent, bg=None, pad=14):
        """Simule des coins arrondis via Frame + padding."""
        bg = bg or self.CARD
        outer = tk.Frame(parent, bg=bg)
        inner = tk.Frame(outer, bg=bg)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return outer, inner

    def _label(self, parent, text, font_size=13, bold=False, color=None, **kw):
        weight = "bold" if bold else "normal"
        color  = color or self.TEXT
        return tk.Label(parent, text=text,
                        font=("Segoe UI", font_size, weight),
                        bg=parent.cget("bg"), fg=color, **kw)

    def _entry(self, parent, textvariable=None, width=8, show=""):
        return tk.Entry(parent, textvariable=textvariable, width=width,
                        show=show, bg=self.CARD2, fg=self.TEXT,
                        font=("Segoe UI", 11), relief="flat", bd=0,
                        insertbackground=self.TEXT,
                        highlightthickness=1,
                        highlightbackground=self.CARD3,
                        highlightcolor=self.ACCENT)

    def _icon_btn(self, parent, icon, text, cmd, width=None):
        """Bouton sobre avec icône + texte, style capture d'écran."""
        bg = self.CARD2
        kw = {"width": width} if width else {}
        b = tk.Button(parent, text=f"{icon}  {text}", command=cmd,
                      bg=bg, fg=self.TEXT,
                      activebackground=self.CARD3,
                      activeforeground=self.TEXT,
                      font=("Segoe UI", 11), relief="flat", bd=0,
                      cursor="hand2", padx=16, pady=9, **kw)
        b.bind("<Enter>", lambda e: b.config(bg=self.CARD3))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    def _btn(self, parent, text, cmd, color=None, **kw):
        bg = color or self.CARD2
        b = tk.Button(parent, text=text, command=cmd,
                      bg=bg, fg=self.TEXT,
                      activebackground=self._lighten(bg),
                      activeforeground=self.TEXT,
                      font=("Segoe UI", 11), relief="flat", bd=0,
                      cursor="hand2", padx=14, pady=8, **kw)
        b.bind("<Enter>", lambda e: b.config(bg=self._lighten(bg)))
        b.bind("<Leave>", lambda e: b.config(bg=bg))
        return b

    def _flat_btn(self, parent, text, cmd, color=None, fg=None):
        return self._btn(parent, text, cmd, color=color)

    # ─────────────────────────────────────────────────────────────────────────
    # Root layout
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._style_ttk()

        # ── Header fixe (ne scroll pas) ───────────────────────────────────────
        hdr = tk.Frame(self, bg=self.BG)
        hdr.pack(fill="x", padx=28, pady=(18, 10))

        lf = tk.Frame(hdr, bg=self.BG)
        lf.pack(side="left")
        self._label(lf, "Couleur des LEDs", 22, bold=True).pack(anchor="w")
        self._label(lf, "Personnalisez la couleur et les effets de vos LEDs",
                    11, color=self.MUTED2).pack(anchor="w", pady=(2, 0))

        rf = tk.Frame(hdr, bg=self.BG)
        rf.pack(side="right", anchor="center")
        self.theme_btn = tk.Button(rf, text="⚙", command=self._open_settings,
                                   bg=self.BG, fg=self.MUTED2,
                                   activebackground=self.CARD,
                                   activeforeground=self.TEXT,
                                   font=("Segoe UI", 16), relief="flat",
                                   bd=0, cursor="hand2", padx=8, pady=4)
        self.theme_btn.pack(side="right", padx=(12, 0))
        self.theme_btn.bind("<Enter>", lambda e: self.theme_btn.config(fg=self.TEXT))
        self.theme_btn.bind("<Leave>", lambda e: self.theme_btn.config(fg=self.MUTED2))
        self.status_dot = self._label(rf, "●", 13, color=self.DANGER)
        self.status_dot.pack(side="left")
        self.status_lbl = self._label(rf, "  Déconnecté", 13)
        self.status_lbl.pack(side="left")

        # ── Zone scrollable ───────────────────────────────────────────────────
        scroll_outer = tk.Frame(self, bg=self.BG)
        scroll_outer.pack(fill="both", expand=True)

        self._scroll_canvas = tk.Canvas(scroll_outer, bg=self.BG,
                                        bd=0, highlightthickness=0)
        vbar = tk.Scrollbar(scroll_outer, orient="vertical",
                            command=self._scroll_canvas.yview)
        self._scroll_canvas.configure(yscrollcommand=vbar.set)

        vbar.pack(side="right", fill="y")
        self._scroll_canvas.pack(side="left", fill="both", expand=True)

        # Frame intérieure qui contiendra tout le contenu scrollable
        self._scroll_inner = tk.Frame(self._scroll_canvas, bg=self.BG)
        self._scroll_window = self._scroll_canvas.create_window(
            (0, 0), window=self._scroll_inner, anchor="nw")

        # Ajuster la scrollregion quand le contenu change de taille
        def _on_frame_configure(e):
            self._scroll_canvas.configure(
                scrollregion=self._scroll_canvas.bbox("all"))
        self._scroll_inner.bind("<Configure>", _on_frame_configure)

        # Étirer la frame intérieure à la largeur du canvas
        def _on_canvas_configure(e):
            self._scroll_canvas.itemconfig(
                self._scroll_window, width=e.width)
        self._scroll_canvas.bind("<Configure>", _on_canvas_configure)

        # Scroll à la molette
        def _on_mousewheel(e):
            self._scroll_canvas.yview_scroll(int(-1*(e.delta/120)), "units")
        self._scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # ── Contenu scrollable ────────────────────────────────────────────────
        content = self._scroll_inner

        # Grille principale
        body = tk.Frame(content, bg=self.BG)
        body.pack(fill="x", padx=20, pady=(0, 6))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=0)
        body.rowconfigure(1, weight=0)
        body.rowconfigure(2, weight=0)

        self._build_preview(body)
        self._build_color(body)
        self._build_power(body)
        self._build_brightness(body)
        self._build_right_col(body)
        self._build_immersion(body)

        # Barre de connexion EN BAS du contenu scrollable
        self._build_connection_bar(content)

    # ─────────────────────────────────────────────────────────────────────────
    # Cartes individuelles
    # ─────────────────────────────────────────────────────────────────────────
    def _card_frame(self, parent, row, col, title="",
                    rowspan=1, colspan=1, expand=True):
        outer = tk.Frame(parent, bg=self.CARD)
        outer.grid(row=row, column=col, rowspan=rowspan, columnspan=colspan,
                   sticky="nsew", padx=7, pady=7)
        if title:
            self._label(outer, title, 13, bold=True).pack(
                anchor="w", padx=18, pady=(16, 6))
        inner = tk.Frame(outer, bg=self.CARD)
        inner.pack(fill="both", expand=expand, padx=18, pady=(0, 16))
        return inner

    # ── Aperçu ────────────────────────────────────────────────────────────────
    def _build_preview(self, body):
        inner = self._card_frame(body, 0, 0, "Aperçu")
        self.canvas_prev = tk.Canvas(inner, height=80, bg="#0D0D10",
                                     bd=0, highlightthickness=0)
        self.canvas_prev.pack(fill="x")

    # ── Couleur ───────────────────────────────────────────────────────────────
    def _build_color(self, body):
        outer = tk.Frame(body, bg=self.CARD)
        outer.grid(row=1, column=0, sticky="nsew", padx=7, pady=7)
        self._label(outer, "Couleur", 13, bold=True).pack(
            anchor="w", padx=18, pady=(16, 6))
        inner = tk.Frame(outer, bg=self.CARD)
        inner.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        inner.columnconfigure(0, weight=0)
        inner.columnconfigure(1, weight=1)

        # ── Colonne gauche : SV picker ────────────────────────────────────────
        lc = tk.Frame(inner, bg=self.CARD)
        lc.grid(row=0, column=0, sticky="nw", padx=(0, 20))

        SV = 190
        self._sv_size = SV
        self.sv_canvas = tk.Canvas(lc, width=SV, height=SV,
                                   bd=0, highlightthickness=0,
                                   cursor="crosshair")
        self.sv_canvas.pack()
        self.sv_canvas.bind("<Button-1>",  self._on_sv_click)
        self.sv_canvas.bind("<B1-Motion>", self._on_sv_click)
        self._draw_sv_gradient()
        self._sv_cursor = self.sv_canvas.create_oval(
            SV-10, 4, SV+10, 24, outline="white", width=2, fill="")

        # Barre hue (arrondie visuellement)
        HH = 16
        self.hue_canvas = tk.Canvas(lc, width=SV, height=HH,
                                    bd=0, highlightthickness=0,
                                    cursor="hand2")
        self.hue_canvas.pack(pady=(10, 0))
        self.hue_canvas.bind("<Button-1>",  self._on_hue_click)
        self.hue_canvas.bind("<B1-Motion>", self._on_hue_click)
        self._draw_hue_bar(SV, HH)
        self._hue_cursor = self.hue_canvas.create_oval(
            -9, 0, 9, HH, outline="white", width=2, fill="")

        # ── Colonne droite : champs ───────────────────────────────────────────
        rc = tk.Frame(inner, bg=self.CARD)
        rc.grid(row=0, column=1, sticky="nw")

        # R V B
        rgb_f = tk.Frame(rc, bg=self.CARD)
        rgb_f.pack(anchor="w", pady=(4, 0))
        for lbl_txt, var in [("R", self.rgb_r),
                              ("V", self.rgb_g),
                              ("B", self.rgb_b)]:
            cf = tk.Frame(rgb_f, bg=self.CARD)
            cf.pack(side="left", padx=(0, 12))
            self._label(cf, lbl_txt, 10, color=self.MUTED2).pack(anchor="w")
            sp = tk.Spinbox(cf, from_=0, to=255, textvariable=var,
                            width=5, bg=self.CARD2, fg=self.TEXT,
                            buttonbackground=self.CARD2,
                            relief="flat", bd=0, font=("Segoe UI", 12),
                            highlightthickness=1,
                            highlightbackground=self.CARD3,
                            highlightcolor=self.ACCENT,
                            insertbackground=self.TEXT,
                            command=self._on_rgb_spinbox)
            sp.pack()
            sp.bind("<Return>",   lambda e: self._on_rgb_spinbox())
            sp.bind("<FocusOut>", lambda e: self._on_rgb_spinbox())

        # Hex
        hex_f = tk.Frame(rc, bg=self.CARD)
        hex_f.pack(anchor="w", pady=(16, 0))
        self._label(hex_f, "Hex", 10, color=self.MUTED2).pack(side="left",
                                                                padx=(0, 10))
        he = self._entry(hex_f, textvariable=self.hex_var, width=10)
        he.pack(side="left")
        he.bind("<Return>",   lambda e: self._on_hex_entry())
        he.bind("<FocusOut>", lambda e: self._on_hex_entry())

        # Préréglages
        self._label(rc, "Préréglages", 10, color=self.MUTED2).pack(
            anchor="w", pady=(20, 6))
        pf = tk.Frame(rc, bg=self.CARD)
        pf.pack(anchor="w")
        presets = ["#FF0000","#FF8C00","#FFD700","#22C55E",
                   "#06B6D4","#3B82F6","#8B5CF6","#EC4899","#FFFFFF"]
        for hx in presets:
            c = tk.Canvas(pf, width=36, height=36, bg=hx,
                          bd=0, highlightthickness=0, cursor="hand2")
            c.pack(side="left", padx=3, pady=2)
            c.bind("<Button-1>", lambda e, col=hx: self._quick_color(col))

    # ─── SV / Hue picker ─────────────────────────────────────────────────────
    def _draw_sv_gradient(self):
        from tkinter import PhotoImage
        self.sv_canvas.delete("gradient")
        size = self._sv_size
        img = PhotoImage(width=size, height=size)
        rows = []
        for y in range(size):
            v = 1.0 - y / size
            row = []
            for x in range(size):
                s = x / size
                r, g, b = self._hsv_to_rgb(self._hsv_h, s, v)
                row.append(f"#{r:02X}{g:02X}{b:02X}")
            rows.append("{" + " ".join(row) + "}")
        img.put(" ".join(rows))
        self.sv_canvas._photo = img
        self.sv_canvas.create_image(0, 0, anchor="nw", image=img, tags="gradient")
        self.sv_canvas.tag_lower("gradient")

    def _draw_hue_bar(self, w, h):
        self.hue_canvas.delete("all")
        for x in range(w):
            r, g, b = self._hsv_to_rgb(x / w * 360, 1.0, 1.0)
            self.hue_canvas.create_line(x, 0, x, h, fill=f"#{r:02X}{g:02X}{b:02X}")
        self._hue_cursor = self.hue_canvas.create_oval(
            -9, 0, 9, h, outline="white", width=2, fill="")

    def _on_hue_click(self, event):
        w = max(1, self.hue_canvas.winfo_width())
        x = max(0, min(event.x, w-1))
        self._hsv_h = x / w * 360
        self.hue_canvas.coords(self._hue_cursor, x-9, 0, x+9,
                               self.hue_canvas.winfo_height())
        self._draw_sv_gradient()
        self._update_sv_cursor()
        self._apply_hsv()

    def _on_sv_click(self, event):
        size = self._sv_size
        x = max(0, min(event.x, size-1))
        y = max(0, min(event.y, size-1))
        self._hsv_s = x / size
        self._hsv_v = 1.0 - y / size
        self._update_sv_cursor()
        self._apply_hsv()

    def _update_sv_cursor(self):
        size = self._sv_size
        cx = self._hsv_s * size
        cy = (1.0 - self._hsv_v) * size
        self.sv_canvas.coords(self._sv_cursor, cx-10, cy-10, cx+10, cy+10)
        self.sv_canvas.tag_raise(self._sv_cursor)

    def _apply_hsv(self):
        r, g, b = self._hsv_to_rgb(self._hsv_h, self._hsv_s, self._hsv_v)
        self._set_color_from_rgb(r, g, b, source="hsv")

    def _on_rgb_spinbox(self):
        if self._updating_color: return
        try:
            r = max(0, min(255, self.rgb_r.get()))
            g = max(0, min(255, self.rgb_g.get()))
            b = max(0, min(255, self.rgb_b.get()))
            self._set_color_from_rgb(r, g, b, source="rgb")
        except Exception: pass

    def _on_hex_entry(self):
        if self._updating_color: return
        hx = self.hex_var.get().strip()
        if not hx.startswith("#"): hx = "#" + hx
        try:
            r, g, b = self._hex_to_rgb(hx)
            self._set_color_from_rgb(r, g, b, source="hex")
        except Exception: pass

    def _set_color_from_rgb(self, r, g, b, source=""):
        self._updating_color = True
        self.color1 = f"#{r:02X}{g:02X}{b:02X}"
        if source != "rgb":
            self.rgb_r.set(r); self.rgb_g.set(g); self.rgb_b.set(b)
        if source != "hex":
            self.hex_var.set(self.color1)
        if source != "hsv":
            self._hsv_h, self._hsv_s, self._hsv_v = self._rgb_to_hsv(r, g, b)
            self._draw_sv_gradient()
            self._update_sv_cursor()
            hw = max(1, self.hue_canvas.winfo_width())
            hx2 = int(self._hsv_h / 360 * hw)
            self.hue_canvas.coords(self._hue_cursor, hx2-9, 0, hx2+9,
                                   self.hue_canvas.winfo_height())
        self._updating_color = False
        self._send_color1()

    @staticmethod
    def _rgb_to_hsv(r, g, b):
        r, g, b = r/255.0, g/255.0, b/255.0
        mx = max(r,g,b); mn = min(r,g,b); d = mx - mn
        if d == 0: h = 0
        elif mx == r: h = 60 * ((g-b)/d % 6)
        elif mx == g: h = 60 * ((b-r)/d + 2)
        else:         h = 60 * ((r-g)/d + 4)
        return h, (0 if mx==0 else d/mx), mx

    # ── Luminosité ────────────────────────────────────────────────────────────
    def _build_brightness(self, body):
        inner = self._card_frame(body, 0, 1, "Luminosité", expand=False)
        self._slider_row(inner, self.brightness, 0, 255,
                         "☀", self._send_brightness, pct=True)

    # ── Colonne droite (Nb LEDs + Effets empilés) ─────────────────────────────
    def _build_right_col(self, body):
        rc = tk.Frame(body, bg=self.BG)
        rc.grid(row=1, column=1, sticky="nsew", padx=7, pady=7)
        rc.columnconfigure(0, weight=1)
        rc.rowconfigure(0, weight=1)
        rc.rowconfigure(1, weight=1)

        # Nombre de LEDs
        nl_outer = tk.Frame(rc, bg=self.CARD)
        nl_outer.grid(row=0, column=0, sticky="nsew", pady=(0, 7))
        nl_outer.columnconfigure(0, weight=1)
        self._label(nl_outer, "Nombre de LEDs", 13, bold=True).pack(
            anchor="w", padx=18, pady=(16, 6))
        nl_inner = tk.Frame(nl_outer, bg=self.CARD)
        nl_inner.pack(fill="x", padx=18, pady=(0, 16))
        self._slider_row(nl_inner, self.led_count, 1, 300,
                         "◉", self._send_led_count)

        # Effets
        eff_outer = tk.Frame(rc, bg=self.CARD)
        eff_outer.grid(row=1, column=0, sticky="nsew")
        eff_outer.columnconfigure(0, weight=1)
        self._label(eff_outer, "Effets", 13, bold=True).pack(
            anchor="w", padx=18, pady=(16, 6))
        eff_inner = tk.Frame(eff_outer, bg=self.CARD)
        eff_inner.pack(fill="x", expand=True, padx=18, pady=(0, 16))

        # Ligne combobox + vitesse
        row_eff = tk.Frame(eff_inner, bg=self.CARD)
        row_eff.pack(fill="x")
        row_eff.columnconfigure(1, weight=1)

        # Combobox effet
        self.eff_combo = ttk.Combobox(row_eff, textvariable=self.effect_var,
                                      values=list(EFFECTS.keys()),
                                      state="readonly", width=16,
                                      font=("Segoe UI", 11))
        self.eff_combo.pack(side="left", padx=(0, 16))
        self.eff_combo.bind("<<ComboboxSelected>>", self._on_effect_selected)

        # Vitesse label + slider + champ
        spd_f = tk.Frame(row_eff, bg=self.CARD)
        spd_f.pack(side="left", fill="x", expand=True)
        self._label(spd_f, "Vitesse", 10, color=self.MUTED2).pack(anchor="w")

        spd_row = tk.Frame(spd_f, bg=self.CARD)
        spd_row.pack(fill="x")

        self._spd_pct = tk.IntVar(value=50)
        spd_sl = ttk.Scale(spd_row, from_=1, to=255,
                           variable=self.speed_var,
                           orient="horizontal",
                           style="W.Horizontal.TScale",
                           command=lambda v: [
                               self._send_effect(),
                               self._spd_pct.set(int(float(v)*100/255)),
                               spd_lbl.config(text=f"{int(float(v)*100/255)}")])
        spd_sl.pack(side="left", fill="x", expand=True)

        spd_box = tk.Frame(spd_row, bg=self.CARD2,
                           highlightthickness=1,
                           highlightbackground=self.CARD3)
        spd_box.pack(side="left", padx=(8, 0))
        spd_lbl = tk.Label(spd_box, text="50", width=3, anchor="center",
                           bg=self.CARD2, fg=self.TEXT,
                           font=("Segoe UI", 11))
        spd_lbl.pack(side="left", padx=5, pady=4)
        tk.Label(spd_box, text="%", bg=self.CARD2, fg=self.MUTED2,
                 font=("Segoe UI", 11)).pack(side="left", padx=(0, 5), pady=4)

    # ── Alimentation ──────────────────────────────────────────────────────────
    def _build_power(self, body):
        inner = self._card_frame(body, 2, 0, "Alimentation", expand=False)
        pf = tk.Frame(inner, bg=self.CARD)
        pf.pack(fill="x")
        self.power_btn = self._icon_btn(pf, "⏻", "Allumer", self._toggle_power)
        self.power_btn.pack(side="left", padx=(0, 12))
        self._icon_btn(pf, "↺", "Redémarrer ESP32", self._send_reset).pack(side="left")

    # ── Mode immersion + Ambilight ────────────────────────────────────────────
    def _build_immersion(self, body):
        outer = tk.Frame(body, bg=self.CARD)
        outer.grid(row=2, column=1, sticky="nsew", padx=7, pady=7)

        inner = tk.Frame(outer, bg=self.CARD)
        inner.pack(fill="both", expand=True, padx=16, pady=12)
        inner.columnconfigure(0, weight=1)
        inner.columnconfigure(1, weight=0)   # séparateur
        inner.columnconfigure(2, weight=1)

        # ── Gauche : Mode Immersion ───────────────────────────────────────────
        lf = tk.Frame(inner, bg=self.CARD)
        lf.grid(row=0, column=0, sticky="nsew")

        tk.Label(lf, text="Mode immersion", font=("Segoe UI", 12, "bold"),
                 bg=self.CARD, fg=self.TEXT).pack(anchor="w")
        tk.Label(lf, text="Couleur dominante de l'écran → LEDs",
                 font=("Segoe UI", 9), bg=self.CARD, fg=self.MUTED2
                 ).pack(anchor="w", pady=(2, 8))

        btns_l = tk.Frame(lf, bg=self.CARD)
        btns_l.pack(anchor="w")
        self.imm_btn = self._icon_btn(btns_l, "🖥", "Activer immersion",
                                      self._toggle_immersion)
        self.imm_btn.pack(side="left", padx=(0, 8))

        self._audio_enabled = tk.BooleanVar(value=True)
        self.audio_toggle_btn = tk.Button(
            btns_l, text="🎵 Audio ON", command=self._toggle_audio,
            bg=self.SUCCESS, fg="white",
            activebackground=self.SUCCESS,
            font=("Segoe UI", 10, "bold"),
            relief="flat", bd=0, cursor="hand2", padx=12, pady=6)
        self.audio_toggle_btn.pack(side="left")

        indicators = tk.Frame(lf, bg=self.CARD)
        indicators.pack(anchor="w", pady=(8, 0))
        tk.Label(indicators, text="Couleur :", font=("Segoe UI", 9),
                 bg=self.CARD, fg=self.MUTED2).pack(side="left")
        self.imm_swatch = tk.Canvas(indicators, width=50, height=22,
                                    bg="#000000", bd=0,
                                    highlightthickness=1,
                                    highlightbackground=self.CARD3)
        self.imm_swatch.pack(side="left", padx=(4, 12))
        tk.Label(indicators, text="Audio :", font=("Segoe UI", 9),
                 bg=self.CARD, fg=self.MUTED2).pack(side="left")
        self.audio_bar = tk.Canvas(indicators, width=100, height=16,
                                   bg=self.CARD2, bd=0,
                                   highlightthickness=1,
                                   highlightbackground=self.CARD3)
        self.audio_bar.pack(side="left", padx=(4, 0))

        # ── Séparateur vertical ───────────────────────────────────────────────
        sep = tk.Frame(inner, bg=self.CARD3, width=1)
        sep.grid(row=0, column=1, sticky="ns", padx=16)

        # ── Droite : Mode Ambilight ───────────────────────────────────────────
        rf = tk.Frame(inner, bg=self.CARD)
        rf.grid(row=0, column=2, sticky="nsew")

        tk.Label(rf, text="Mode Ambilight", font=("Segoe UI", 12, "bold"),
                 bg=self.CARD, fg=self.TEXT).pack(anchor="w")
        tk.Label(rf, text="Bords de l'écran → zones de LEDs",
                 font=("Segoe UI", 9), bg=self.CARD, fg=self.MUTED2
                 ).pack(anchor="w", pady=(2, 8))

        btns_r = tk.Frame(rf, bg=self.CARD)
        btns_r.pack(anchor="w")
        self.amb_btn = self._icon_btn(btns_r, "📺", "Activer Ambilight",
                                      self._toggle_ambilight)
        self.amb_btn.pack(side="left", padx=(0, 8))

        # Nb de zones
        zone_f = tk.Frame(rf, bg=self.CARD)
        zone_f.pack(anchor="w", pady=(8, 0))
        tk.Label(zone_f, text="Zones :", font=("Segoe UI", 9),
                 bg=self.CARD, fg=self.MUTED2).pack(side="left")
        self.amb_zones_var = tk.IntVar(value=10)
        sp = tk.Spinbox(zone_f, from_=4, to=60,
                        textvariable=self.amb_zones_var, width=4,
                        bg=self.CARD2, fg=self.TEXT,
                        buttonbackground=self.CARD2,
                        relief="flat", bd=0, font=("Segoe UI", 10),
                        highlightthickness=0,
                        insertbackground=self.TEXT)
        sp.pack(side="left", padx=(6, 12))

        # Lissage
        self.amb_smooth_var = tk.BooleanVar(value=True)
        tk.Checkbutton(zone_f, text="Lissage",
                       variable=self.amb_smooth_var,
                       bg=self.CARD, fg=self.TEXT,
                       selectcolor=self.CARD2,
                       activebackground=self.CARD,
                       activeforeground=self.TEXT,
                       font=("Segoe UI", 9),
                       relief="flat", bd=0).pack(side="left")

        # Mini aperçu ambilight (rectangles de couleur)
        self.amb_preview = tk.Canvas(rf, width=180, height=30,
                                     bg=self.CARD2, bd=0,
                                     highlightthickness=1,
                                     highlightbackground=self.CARD3)
        self.amb_preview.pack(anchor="w", pady=(8, 0))

    # ── Slider row ────────────────────────────────────────────────────────────
    def _slider_row(self, parent, var, lo, hi, icon, cmd, pct=False, unit=""):
        f = tk.Frame(parent, bg=self.CARD)
        f.pack(fill="x", pady=2)

        # Icône fixe à gauche
        tk.Label(f, text=icon, font=("Segoe UI", 14),
                 bg=self.CARD, fg=self.MUTED2,
                 width=2).pack(side="left", padx=(0, 6))

        # Boîte valeur + spinbox à droite (taille fixe)
        val_box = tk.Frame(f, bg=self.CARD2,
                           highlightthickness=1,
                           highlightbackground=self.CARD3)
        val_box.pack(side="right", padx=(10, 0))

        init_disp = int(var.get() * 100 / 255) if pct else int(var.get())
        spin_var = tk.IntVar(value=init_disp)
        max_s = 100 if pct else hi
        min_s = 0 if pct else lo

        def _spin_changed():
            try:
                sv = int(spin_var.get())
                sv = max(min_s, min(max_s, sv))
                spin_var.set(sv)
                raw = sv * 255 // 100 if pct else sv
                var.set(max(lo, min(hi, raw)))
                cmd()
            except Exception:
                pass

        sp = tk.Spinbox(val_box, from_=min_s, to=max_s,
                        textvariable=spin_var, width=5,
                        bg=self.CARD2, fg=self.TEXT,
                        buttonbackground=self.CARD2,
                        relief="flat", bd=0, font=("Segoe UI", 12),
                        highlightthickness=0,
                        insertbackground=self.TEXT,
                        command=_spin_changed)
        sp.pack(side="left", padx=(8, 2), pady=5)
        sp.bind("<Return>",   lambda e: _spin_changed())
        sp.bind("<FocusOut>", lambda e: _spin_changed())

        if pct:
            tk.Label(val_box, text="%", font=("Segoe UI", 11),
                     bg=self.CARD2, fg=self.MUTED2,
                     width=1).pack(side="left", padx=(0, 6), pady=5)

        # Slider — prend tout l'espace restant au milieu
        def _on_slide(v):
            disp = int(float(v) * 100 / 255) if pct else int(float(v))
            spin_var.set(disp)
            cmd()

        sl = ttk.Scale(f, from_=lo, to=hi, variable=var,
                       orient="horizontal", style="W.Horizontal.TScale",
                       command=_on_slide)
        sl.pack(side="left", fill="x", expand=True)

    # ── Connexion (barre du bas) ──────────────────────────────────────────────
    # ── Connexion (barre du bas dans le scroll) ───────────────────────────────
    def _build_connection_bar(self, parent=None):
        if parent is None:
            parent = self
        bar = tk.Frame(parent, bg=self.CARD)
        bar.pack(fill="x", padx=20, pady=(6, 20))

        # Titre
        self._label(bar, "Connexion", 13, bold=True).pack(
            anchor="w", padx=16, pady=(14, 6))

        nb = ttk.Notebook(bar)
        nb.pack(fill="x", padx=12, pady=(0, 8))

        # RP2040 EN PREMIER
        rp_f   = tk.Frame(nb, bg=self.CARD, padx=10, pady=8)
        ble_f  = tk.Frame(nb, bg=self.CARD, padx=10, pady=8)
        ser_f  = tk.Frame(nb, bg=self.CARD, padx=10, pady=8)
        wifi_f = tk.Frame(nb, bg=self.CARD, padx=10, pady=8)
        nb.add(rp_f,   text=" 🔵 RP2040 ")
        nb.add(ble_f,  text=" 📡 Bluetooth BLE ")
        nb.add(ser_f,  text=" 🔌 Serial / USB ")
        nb.add(wifi_f, text=" 🌐 WiFi ")

        self._build_rp2040_tab(rp_f)
        self._build_ble_tab(ble_f)
        self._build_serial_tab(ser_f)
        self._build_wifi_tab(wifi_f)

        self.log_text = tk.Text(bar, height=3, bg=self.CARD2, fg=self.MUTED,
                                font=("Consolas", 9), relief="flat",
                                state="disabled", wrap="word",
                                bd=0, highlightthickness=0)
        self.log_text.pack(fill="x", padx=12, pady=(0, 12))

    def _build_rp2040_tab(self, f):
        P = self.PALETTE
        if not SERIAL_AVAILABLE:
            tk.Label(f, text="⚠ pip install pyserial",
                     bg=self.CARD, fg=self.WARN, font=("Segoe UI", 10)).pack()
            return

        # Info
        info = tk.Frame(f, bg=self.CARD2)
        info.pack(fill="x", pady=(0, 8))
        tk.Label(info, text="🔵  Raspberry Pi Pico / RP2040",
                 font=("Segoe UI", 10, "bold"),
                 bg=self.CARD2, fg=self.ACCENT).pack(anchor="w", padx=8, pady=(6, 2))
        tk.Label(info,
                 text="Connectez le Pico en USB — il apparaît comme port COM/ttyACM",
                 font=("Segoe UI", 9), bg=self.CARD2, fg=self.MUTED2,
                 wraplength=480, justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        # Port + Baud
        row = tk.Frame(f, bg=self.CARD)
        row.pack(fill="x", pady=(0, 6))

        tk.Label(row, text="Port :", bg=self.CARD, fg=self.TEXT,
                 font=("Segoe UI", 10)).pack(side="left")
        self.rp_port_combo = ttk.Combobox(row, width=16, state="readonly",
                                           font=("Segoe UI", 10))
        self.rp_port_combo.pack(side="left", padx=(4, 8))

        tk.Label(row, text="Baud :", bg=self.CARD, fg=self.TEXT,
                 font=("Segoe UI", 10)).pack(side="left")
        self.rp_baud_combo = ttk.Combobox(row, width=9, state="readonly",
                                           font=("Segoe UI", 10),
                                           values=["9600","19200","38400",
                                                   "57600","115200","230400"])
        self.rp_baud_combo.set("115200")
        self.rp_baud_combo.pack(side="left", padx=(4, 0))

        tk.Button(row, text="↺", command=self._refresh_rp2040_ports,
                  bg=self.CARD2, fg=self.TEXT, relief="flat",
                  font=("Segoe UI", 10), cursor="hand2",
                  padx=8, pady=4).pack(side="left", padx=8)

        self._refresh_rp2040_ports()

        bf = tk.Frame(f, bg=self.CARD)
        bf.pack(fill="x")
        self._btn(bf, "🔵 Connecter RP2040", self._connect_rp2040,
                  color=self.ACCENT).pack(side="left", padx=(0, 6))
        self._btn(bf, "✕ Déconnecter", self._disconnect,
                  color=self.DANGER).pack(side="right")

    def _refresh_rp2040_ports(self):
        devices = self.bt.scan_rp2040()
        ports = [d for _, d in devices]
        labels = [lbl for lbl, _ in devices]
        self.rp_port_combo["values"] = ports
        if ports:
            self.rp_port_combo.set(ports[0])
        self._rp2040_port_labels = labels

    def _connect_rp2040(self):
        port = self.rp_port_combo.get()
        if not port:
            messagebox.showwarning("RP2040", "Sélectionnez un port.")
            return
        try:
            baud = int(self.rp_baud_combo.get())
        except ValueError:
            baud = 115200
        self.bt.connect_rp2040(port, baud,
                                on_connect=self._on_connected,
                                on_disconnect=self._on_disconnected)

    def _build_ble_tab(self, f):
        if not BLEAK_AVAILABLE:
            tk.Label(f, text="⚠ pip install bleak",
                     bg=self.CARD, fg=self.WARN, font=("Segoe UI", 10)).pack()
            return
        self.ble_list = tk.Listbox(f, height=3, bg=self.CARD2, fg=self.TEXT,
                                   selectbackground=self.ACCENT,
                                   font=("Consolas", 9), relief="flat",
                                   bd=0, activestyle="none")
        self.ble_list.pack(fill="x", pady=(0, 6))
        bf = tk.Frame(f, bg=self.CARD); bf.pack(fill="x")
        self._btn(bf, "🔍 Scanner",   self._scan_ble).pack(side="left", padx=(0,6))
        self._btn(bf, "🔗 Connecter", self._connect_ble,
                  color=self.SUCCESS).pack(side="left", padx=(0,6))
        self._btn(bf, "✕ Déconnecter", self._disconnect,
                  color=self.DANGER).pack(side="right")

    def _build_serial_tab(self, f):
        if not SERIAL_AVAILABLE:
            tk.Label(f, text="⚠ pip install pyserial",
                     bg=self.CARD, fg=self.WARN, font=("Segoe UI", 10)).pack()
            return
        pf = tk.Frame(f, bg=self.CARD); pf.pack(fill="x", pady=(0,6))
        tk.Label(pf, text="Port :", bg=self.CARD, fg=self.TEXT,
                 font=("Segoe UI", 10)).pack(side="left")
        self.serial_combo = ttk.Combobox(pf, width=14, state="readonly",
                                         font=("Segoe UI", 10))
        self.serial_combo.pack(side="left", padx=6)
        self._btn(pf, "↺", self._refresh_ports).pack(side="left")
        bf = tk.Frame(f, bg=self.CARD); bf.pack(fill="x")
        self._btn(bf, "🔗 Connecter", self._connect_serial,
                  color=self.SUCCESS).pack(side="left", padx=(0,6))
        self._btn(bf, "✕ Déconnecter", self._disconnect,
                  color=self.DANGER).pack(side="right")
        self._refresh_ports()

    def _build_wifi_tab(self, f):
        # Config firmware
        fw = tk.Frame(f, bg=self.CARD2)
        fw.pack(fill="x", pady=(0, 8))
        tk.Label(fw, text="Config firmware ESP32", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD2, fg=self.ACCENT).pack(anchor="w", padx=8, pady=(6,2))
        for lbl_txt, attr, default, show in [
            ("SSID :",        "fw_ssid",     "VOTRE_SSID",           ""),
            ("Mot de passe :", "fw_pass",    "VOTRE_MOT_DE_PASSE",   "●"),
            ("Port TCP :",    "fw_tcp_port", "1234",                  ""),
        ]:
            row = tk.Frame(fw, bg=self.CARD2); row.pack(fill="x", padx=8, pady=2)
            tk.Label(row, text=lbl_txt, width=14, anchor="w",
                     bg=self.CARD2, fg=self.MUTED2,
                     font=("Segoe UI", 9)).pack(side="left")
            e = self._entry(row, show=show)
            e.insert(0, default)
            e.pack(side="left", fill="x", expand=True, padx=(4,0))
            setattr(self, attr, e)
            if show:
                def _make_toggle(entry=e):
                    def _t():
                        vis = entry.cget("show") == ""
                        entry.config(show="●" if not vis else "")
                    return _t
                tk.Button(row, text="👁", command=_make_toggle(),
                          bg=self.CARD2, fg=self.MUTED, relief="flat",
                          font=("Segoe UI",10), cursor="hand2", bd=0
                          ).pack(side="left", padx=2)
        tk.Button(fw, text="⬇ Générer firmware .ino configuré",
                  command=self._generate_firmware,
                  bg=self.ACCENT, fg="white", relief="flat",
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  padx=10, pady=5).pack(fill="x", padx=8, pady=(4,8))

        # Connexion
        tk.Label(f, text="Connexion PC → ESP32", font=("Segoe UI", 10, "bold"),
                 bg=self.CARD, fg=self.ACCENT).pack(anchor="w", pady=(2,4))
        row1 = tk.Frame(f, bg=self.CARD); row1.pack(fill="x", pady=(0,4))
        for lbl_txt, attr, default, w in [("IP :",  "wifi_ip",   "192.168.1.100", 16),
                                           ("Port :","wifi_port", "1234",           7)]:
            tk.Label(row1, text=lbl_txt, bg=self.CARD, fg=self.TEXT,
                     font=("Segoe UI",10)).pack(side="left")
            e = self._entry(row1, width=w)
            e.insert(0, default)
            e.pack(side="left", padx=(4,10))
            setattr(self, attr, e)
        tk.Button(row1, text="🔍", command=self._scan_wifi,
                  bg=self.ACCENT, fg="white", relief="flat",
                  font=("Segoe UI",9), cursor="hand2",
                  padx=6, pady=2).pack(side="right")
        self.wifi_list = tk.Listbox(f, height=2, bg=self.CARD2, fg=self.TEXT,
                                    selectbackground=self.ACCENT,
                                    font=("Consolas",9), relief="flat",
                                    bd=0, activestyle="none")
        self.wifi_list.pack(fill="x", pady=(0,6))
        self.wifi_list.bind("<<ListboxSelect>>", self._on_wifi_list_select)
        bf = tk.Frame(f, bg=self.CARD); bf.pack(fill="x")
        self._btn(bf, "🌐 Connecter", self._connect_wifi,
                  color=self.SUCCESS).pack(side="left", padx=(0,6))
        self._btn(bf, "✕ Déconnecter", self._disconnect,
                  color=self.DANGER).pack(side="right")

    # ── Callbacks connexion ───────────────────────────────────────────────────
    def _card(self, parent, title="", **kw):
        outer = tk.Frame(parent, bg=self.CARD, bd=0, highlightthickness=0)
        outer.pack(fill="x", pady=4, **kw)
        if title:
            self._label(outer, title, 12, bold=True).pack(
                anchor="w", padx=14, pady=(10,4))
        inner = tk.Frame(outer, bg=self.CARD)
        inner.pack(fill="x", padx=14, pady=(0,10))
        return inner

        if self.effect_var.get() == "✏ Personnalisé":
            self.open_custom_editor()
        else:
            self._send_effect()

    def _send_custom_leds(self):
        """Envoie les couleurs individuelles au ruban."""
        if not self.custom_led_colors:
            return
        packets = CommandBuilder.custom_leds(self.custom_led_colors)
        for pkt in packets:
            self.bt.send(pkt)
        self.log(f"✏ Personnalisé → {len(self.custom_led_colors)} LEDs envoyées")

    def open_custom_editor(self):
        """Ouvre la fenêtre d'édition LED par LED."""
        n = self.led_count.get()
        # Initialiser avec les couleurs existantes ou rouge par défaut
        if len(self.custom_led_colors) != n:
            self.custom_led_colors = [(255, 0, 0)] * n

        win = tk.Toplevel(self)
        win.title(f"✏ Éditeur personnalisé — {n} LEDs")
        win.geometry("900x560")
        win.configure(bg=self.PALETTE["bg"])
        win.resizable(True, True)

        P = self.PALETTE

        # Header
        hdr = tk.Frame(win, bg=P["panel"], pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"✏  Éditeur LED personnalisé  ({n} LEDs)",
                 font=("Consolas", 13, "bold"),
                 bg=P["panel"], fg=P["accent"]).pack(side="left", padx=16)

        # Toolbar
        tb = tk.Frame(win, bg=P["card"], pady=6)
        tb.pack(fill="x", padx=10, pady=(8, 0))

        # Couleur active
        active_color = tk.StringVar(value="#FF0000")

        def pick_active():
            res = colorchooser.askcolor(color=active_color.get(),
                                        title="Couleur du pinceau")
            if res[1]:
                active_color.set(res[1])
                brush_swatch.config(bg=res[1])

        tk.Label(tb, text="Pinceau :", bg=P["card"],
                 fg=P["muted"], font=("Consolas", 9)).pack(side="left", padx=(8, 4))
        brush_swatch = tk.Canvas(tb, width=32, height=22, bg="#FF0000",
                                  bd=0, highlightthickness=2,
                                  highlightbackground=P["accent"], cursor="hand2")
        brush_swatch.pack(side="left")
        brush_swatch.bind("<Button-1>", lambda e: pick_active())

        # Palette rapide dans la toolbar
        quick = ["#FF0000","#FF8C00","#FFD700","#00FF00","#00BFFF",
                 "#8A2BE2","#FF1493","#FFFFFF","#888888","#000000"]
        for hx in quick:
            b = tk.Canvas(tb, width=20, height=20, bg=hx,
                          bd=0, highlightthickness=1,
                          highlightbackground="#333", cursor="hand2")
            b.pack(side="left", padx=1)
            b.bind("<Button-1>", lambda e, c=hx: [active_color.set(c),
                                                    brush_swatch.config(bg=c)])

        # Boutons outils
        def fill_all():
            hx = active_color.get().lstrip("#")
            r, g, b = int(hx[0:2],16), int(hx[2:4],16), int(hx[4:6],16)
            self.custom_led_colors = [(r,g,b)] * n
            _refresh_grid()

        def fill_gradient():
            hx1 = active_color.get().lstrip("#")
            r1, g1, b1 = int(hx1[0:2],16), int(hx1[2:4],16), int(hx1[4:6],16)
            # Demander une 2e couleur
            res = colorchooser.askcolor(color="#0000FF", title="Couleur de fin du dégradé")
            if not res[1]: return
            hx2 = res[1].lstrip("#")
            r2, g2, b2 = int(hx2[0:2],16), int(hx2[2:4],16), int(hx2[4:6],16)
            for i in range(n):
                f = i / max(n-1, 1)
                self.custom_led_colors[i] = (
                    int(r1 + (r2-r1)*f),
                    int(g1 + (g2-g1)*f),
                    int(b1 + (b2-b1)*f)
                )
            _refresh_grid()

        def clear_all():
            self.custom_led_colors = [(0, 0, 0)] * n
            _refresh_grid()

        tk.Frame(tb, bg=P["card"], width=12).pack(side="left")
        for label, fn, col in [
            ("⬛ Tout effacer", clear_all,    P["muted"]),
            ("▓ Remplir",      fill_all,      P["accent"]),
            ("↔ Dégradé",     fill_gradient,  P["accent2"]),
        ]:
            b2 = tk.Button(tb, text=label, command=fn,
                           bg=col, fg="white", relief="flat",
                           font=("Consolas", 9, "bold"), cursor="hand2",
                           padx=10, pady=4)
            b2.pack(side="left", padx=3)

        # Grille scrollable
        grid_outer = tk.Frame(win, bg=P["bg"])
        grid_outer.pack(fill="both", expand=True, padx=10, pady=8)

        canvas_grid = tk.Canvas(grid_outer, bg=P["bg"],
                                bd=0, highlightthickness=0)
        scroll_y = tk.Scrollbar(grid_outer, orient="vertical",
                                command=canvas_grid.yview)
        scroll_x = tk.Scrollbar(grid_outer, orient="horizontal",
                                command=canvas_grid.xview)
        canvas_grid.configure(yscrollcommand=scroll_y.set,
                               xscrollcommand=scroll_x.set)
        scroll_y.pack(side="right", fill="y")
        scroll_x.pack(side="bottom", fill="x")
        canvas_grid.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas_grid, bg=P["bg"])
        canvas_grid.create_window((0, 0), window=inner, anchor="nw")

        COLS = 20
        LED_SIZE = 36
        led_canvases = []

        def _paint_led(idx):
            hx = active_color.get().lstrip("#")
            r2, g2, b2 = int(hx[0:2],16), int(hx[2:4],16), int(hx[4:6],16)
            self.custom_led_colors[idx] = (r2, g2, b2)
            led_canvases[idx].config(bg=active_color.get())

        def _on_led_click(idx, event):
            _paint_led(idx)

        def _on_led_drag(event):
            w = event.widget.winfo_containing(
                event.widget.winfo_rootx() + event.x,
                event.widget.winfo_rooty() + event.y
            )
            if w in led_canvases:
                _paint_led(led_canvases.index(w))

        def _refresh_grid():
            for i, c in enumerate(led_canvases):
                r2, g2, b2 = self.custom_led_colors[i]
                c.config(bg=f"#{r2:02X}{g2:02X}{b2:02X}")

        for i in range(n):
            row, col = divmod(i, COLS)
            r2, g2, b2 = self.custom_led_colors[i]
            color_hex = f"#{r2:02X}{g2:02X}{b2:02X}"

            cell = tk.Frame(inner, bg=P["bg"], padx=1, pady=1)
            cell.grid(row=row*2, column=col, padx=1, pady=1)

            c = tk.Canvas(cell, width=LED_SIZE, height=LED_SIZE,
                          bg=color_hex, bd=0,
                          highlightthickness=1,
                          highlightbackground="#2D2D4A",
                          cursor="hand2")
            c.pack()
            c.bind("<Button-1>", lambda e, idx=i: _on_led_click(idx, e))
            c.bind("<B1-Motion>", _on_led_drag)
            led_canvases.append(c)

            # Numéro LED
            num = tk.Label(inner, text=str(i+1),
                           bg=P["bg"], fg=P["muted"],
                           font=("Consolas", 6))
            num.grid(row=row*2+1, column=col)

        inner.update_idletasks()
        canvas_grid.config(scrollregion=canvas_grid.bbox("all"))
        canvas_grid.bind("<MouseWheel>",
                         lambda e: canvas_grid.yview_scroll(-1*(e.delta//120), "units"))

        # Footer boutons
        foot = tk.Frame(win, bg=P["panel"], pady=8)
        foot.pack(fill="x", side="bottom")

        def _apply():
            # Envoyer effet custom + couleurs
            eid = EFFECTS["✏ Personnalisé"]
            self.bt.send(CommandBuilder.effect(eid, 128))
            self._send_custom_leds()
            win.destroy()

        def _preview_only():
            _refresh_grid()

        tk.Button(foot, text="✕ Annuler", command=win.destroy,
                  bg=P["danger"], fg="white", relief="flat",
                  font=("Consolas", 10, "bold"), padx=16, pady=6,
                  cursor="hand2").pack(side="left", padx=12)
        tk.Button(foot, text="▶ Appliquer & Envoyer", command=_apply,
                  bg=P["success"], fg="white", relief="flat",
                  font=("Consolas", 10, "bold"), padx=16, pady=6,
                  cursor="hand2").pack(side="right", padx=12)
        tk.Button(foot, text="⟳ Renvoyer", command=self._send_custom_leds,
                  bg=P["accent"], fg="white", relief="flat",
                  font=("Consolas", 10, "bold"), padx=16, pady=6,
                  cursor="hand2").pack(side="right", padx=4)

    def _on_effect_selected(self, event=None):
        if self.effect_var.get() == "✏ Personnalisé":
            self.open_custom_editor()
        else:
            self._send_effect()

    def _on_scan_results(self, devices):
        self.scan_results = devices
        self.ble_list.delete(0, "end")
        if not devices:
            self.ble_list.insert("end", "Aucun périphérique trouvé")
            return
        for name, addr in devices:
            self.ble_list.insert("end", f"{name}  [{addr}]")

    def _on_wifi_scan_done(self, found):
        self.wifi_list.delete(0, "end")
        self._wifi_scan_results = found
        if not found:
            self.wifi_list.insert("end", "Aucun ESP32 trouvé")
        else:
            for name, ip in found:
                self.wifi_list.insert("end", f"{name}  →  {ip}")

    def _on_wifi_list_select(self, event=None):
        sel = self.wifi_list.curselection()
        if not sel or not hasattr(self, "_wifi_scan_results"):
            return
        if sel[0] < len(self._wifi_scan_results):
            _, ip = self._wifi_scan_results[sel[0]]
            self.wifi_ip.delete(0, "end")
            self.wifi_ip.insert(0, ip)

    def _scan_ble(self):
        if not BLEAK_AVAILABLE:
            messagebox.showerror("Erreur", "bleak non installé.\npip install bleak")
            return
        self.ble_list.delete(0, "end")
        self.ble_list.insert("end", "Scan en cours…")
        self.bt.scan_ble(self._on_scan_results)

    def _on_scan_results(self, devices):
        self.scan_results = devices
        self.ble_list.delete(0, "end")
        if not devices:
            self.ble_list.insert("end", "Aucun périphérique trouvé")
            return
        for name, addr in devices:
            self.ble_list.insert("end", f"{name}  [{addr}]")

    def _connect_ble(self):
        sel = self.ble_list.curselection()
        if not sel or not self.scan_results:
            messagebox.showwarning("Sélection", "Sélectionnez un périphérique BLE.")
            return
        _, addr = self.scan_results[sel[0]]
        self.bt.connect_ble(addr, self._on_connected, self._on_disconnected)

    # ── Actions Serial ────────────────────────────────────────────────────────
    def _refresh_ports(self):
        if not SERIAL_AVAILABLE:
            return
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.serial_combo["values"] = ports
        if ports:
            self.serial_combo.set(ports[0])

    def _connect_serial(self):
        if not SERIAL_AVAILABLE:
            messagebox.showerror("Erreur", "pyserial non installé.\npip install pyserial")
            return
        port = self.serial_combo.get()
        if not port:
            messagebox.showwarning("Port", "Sélectionnez un port série.")
            return
        if self.bt.connect_serial(port):
            self._on_connected()

    def _disconnect(self):
        self.bt.disconnect()
        self._on_disconnected()

    # ── Actions WiFi ──────────────────────────────────────────────────────────
    def _scan_wifi(self):
        """Scan mDNS / ping broadcast pour trouver des ESP32 sur le réseau."""
        self.wifi_list.delete(0, "end")
        self.wifi_list.insert("end", "🔍 Scan en cours…")
        def _run():
            import socket, ipaddress
            found = []
            # Essayer de résoudre le nom mDNS de l'ESP32
            for mdns in ["esp32-leds.local", "esp32s3-leds.local", "esp32.local"]:
                try:
                    ip = socket.gethostbyname(mdns)
                    found.append((mdns, ip))
                except Exception:
                    pass
            # Scan du sous-réseau local sur le port configuré
            try:
                port = int(self.wifi_port.get())
                # Récupérer l'IP locale
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
                base = ".".join(local_ip.split(".")[:3])
                for i in range(1, 255):
                    ip = f"{base}.{i}"
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(0.05)
                        if sock.connect_ex((ip, port)) == 0:
                            # Vérifier que c'est bien un ESP32 LED
                            try:
                                sock.sendall(bytes([0xF0]))  # ping
                                resp = sock.recv(4)
                                if resp and resp[0] == 0xF1:
                                    found.append((f"ESP32 ({ip})", ip))
                                else:
                                    found.append((f"Appareil ({ip})", ip))
                            except Exception:
                                found.append((f"Appareil ({ip})", ip))
                        sock.close()
                    except Exception:
                        pass
            except Exception:
                pass
            self.after(0, lambda: self._on_wifi_scan_done(found))
        threading.Thread(target=_run, daemon=True).start()

    def _on_wifi_scan_done(self, found):
        self.wifi_list.delete(0, "end")
        self._wifi_scan_results = found
        if not found:
            self.wifi_list.insert("end", "Aucun ESP32 trouvé")
        else:
            for name, ip in found:
                self.wifi_list.insert("end", f"{name}  →  {ip}")

    def _on_wifi_list_select(self, event=None):
        sel = self.wifi_list.curselection()
        if not sel or not hasattr(self, "_wifi_scan_results"):
            return
        if sel[0] < len(self._wifi_scan_results):
            _, ip = self._wifi_scan_results[sel[0]]
            self.wifi_ip.delete(0, "end")
            self.wifi_ip.insert(0, ip)

    def _connect_wifi(self):
        host = self.wifi_ip.get().strip()
        if not host:
            messagebox.showwarning("WiFi", "Entrez l'adresse IP de l'ESP32.")
            return
        try:
            port = int(self.wifi_port.get())
        except ValueError:
            messagebox.showerror("WiFi", "Port invalide.")
            return
        self.bt.connect_wifi(host, port, self._on_connected, self._on_disconnected)

    def _generate_firmware(self):
        """Génère le fichier .ino avec les credentials WiFi renseignés et propose de le sauvegarder."""
        import tkinter.filedialog as fd
        ssid    = self.fw_ssid.get().strip()
        pwd     = self.fw_pass.get().strip()
        tcp_port = self.fw_tcp_port.get().strip()

        if not ssid:
            messagebox.showwarning("Firmware", "Entrez votre SSID WiFi.")
            return
        try:
            int(tcp_port)
        except ValueError:
            messagebox.showerror("Firmware", "Port TCP invalide.")
            return

        # Lire le firmware embarqué et remplacer les placeholders
        fw = ESP32_FIRMWARE_TEMPLATE \
            .replace("VOTRE_SSID",           ssid) \
            .replace("VOTRE_MOT_DE_PASSE",   pwd) \
            .replace('"1234"',              f'"{tcp_port}"') \
            .replace("const int   WIFI_TCP_PORT = 1234;",
                     f"const int   WIFI_TCP_PORT = {tcp_port};")

        # Aussi mettre à jour le port dans le champ de connexion
        self.wifi_port.delete(0, "end")
        self.wifi_port.insert(0, tcp_port)

        # Sauvegarde
        path = fd.asksaveasfilename(
            title="Enregistrer le firmware",
            defaultextension=".ino",
            initialfile="ESP32_S3_LED_Controller.ino",
            filetypes=[("Arduino sketch", "*.ino"), ("Tous les fichiers", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(fw)
            self.log(f"✅ Firmware généré → {path}")
            messagebox.showinfo(
                "Firmware généré !",
                f"Fichier sauvegardé :\n{path}\n\n"
                f"SSID     : {ssid}\n"
                f"Port TCP : {tcp_port}\n\n"
                "Ouvrez-le dans Arduino IDE et flashez l'ESP32."
            )
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible d'écrire le fichier :\n{e}")
    def _on_connected(self):
        self.after(0, lambda: self.status_lbl.config(
            text=f"● Connecté ({self.bt.mode.upper()})",
            fg=self.PALETTE["success"]
        ))
        self.log("🟢 Connexion établie — prêt à contrôler les LEDs !")
        # Synchroniser l'état courant
        self._send_brightness()
        self._send_led_count()
        self._send_color1()

    def _on_disconnected(self):
        if self._immersion_active:
            self._stop_immersion()
        self.after(0, lambda: self.status_lbl.config(
            text="● Déconnecté", fg=self.PALETTE["danger"]
        ))
        self.log("🔴 Déconnexion.")

    # ── Envoi commandes ───────────────────────────────────────────────────────
    def _send_color1(self):
        r, g, b = self._hex_to_rgb(self.color1)
        self.bt.send(CommandBuilder.color(r, g, b))

    def _send_color2(self):
        r, g, b = self._hex_to_rgb(self.color2)
        self.bt.send(CommandBuilder.color2(r, g, b))

    def _send_brightness(self):
        self.bt.send(CommandBuilder.brightness(self.brightness.get()))

    def _send_led_count(self):
        self.bt.send(CommandBuilder.led_count(self.led_count.get()))

    def _send_effect(self):
        eid = EFFECTS.get(self.effect_var.get(), 0)
        spd = self.speed_var.get()
        self.bt.send(CommandBuilder.effect(eid, spd))

    # ── Paramètres / Thème ────────────────────────────────────────────────────
    def _open_settings(self):
        win = tk.Toplevel(self)
        win.title("Paramètres")
        win.geometry("360x260")
        win.configure(bg=self.BG)
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text="⚙  Paramètres",
                 font=("Segoe UI", 15, "bold"),
                 bg=self.BG, fg=self.TEXT).pack(pady=(20, 4))
        tk.Label(win, text="Personnalisez l'apparence de l'application",
                 font=("Segoe UI", 10),
                 bg=self.BG, fg=self.MUTED2).pack(pady=(0, 20))

        sep = tk.Frame(win, bg=self.CARD3, height=1)
        sep.pack(fill="x", padx=20)

        # Thème
        theme_f = tk.Frame(win, bg=self.BG)
        theme_f.pack(fill="x", padx=24, pady=16)
        tk.Label(theme_f, text="Thème de l'interface",
                 font=("Segoe UI", 11, "bold"),
                 bg=self.BG, fg=self.TEXT).pack(anchor="w")
        tk.Label(theme_f, text="Choisissez entre le mode clair et sombre",
                 font=("Segoe UI", 9),
                 bg=self.BG, fg=self.MUTED2).pack(anchor="w", pady=(2, 10))

        btn_f = tk.Frame(theme_f, bg=self.BG)
        btn_f.pack(anchor="w")

        def _make_theme_btn(label, icon, mode):
            is_active = self._current_theme == mode
            bg = self.ACCENT if is_active else self.CARD2
            fg = "white" if is_active else self.TEXT
            b = tk.Button(btn_f, text=f"{icon}  {label}",
                          bg=bg, fg=fg,
                          activebackground=self._lighten(bg),
                          activeforeground=fg,
                          font=("Segoe UI", 11), relief="flat", bd=0,
                          cursor="hand2", padx=16, pady=8,
                          command=lambda m=mode: [self._apply_theme_live(m),
                                                   win.destroy()])
            return b

        _make_theme_btn("Mode sombre", "🌙", "dark").pack(side="left", padx=(0, 8))
        _make_theme_btn("Mode clair",  "☀",  "light").pack(side="left")

        sep2 = tk.Frame(win, bg=self.CARD3, height=1)
        sep2.pack(fill="x", padx=20, pady=(8, 0))

        tk.Button(win, text="Fermer", command=win.destroy,
                  bg=self.CARD2, fg=self.TEXT,
                  activebackground=self.CARD3, activeforeground=self.TEXT,
                  font=("Segoe UI", 11), relief="flat", bd=0,
                  cursor="hand2", padx=20, pady=8).pack(pady=14)

    def _apply_theme_live(self, name):
        """Applique le thème et reconstruit toute l'interface."""
        self._current_theme = name
        self._apply_theme(name)
        # Détruire et reconstruire l'UI
        for widget in self.winfo_children():
            widget.destroy()
        self.configure(bg=self.BG)
        self._style_ttk()
        self._build_ui()
        self._start_preview_loop()
        self.log(f"🎨 Thème {'sombre 🌙' if name == 'dark' else 'clair ☀'} activé")

    def _toggle_audio(self):
        enabled = not self._audio_enabled.get()
        self._audio_enabled.set(enabled)
        if enabled:
            self.audio_toggle_btn.config(
                text="🎵 Audio ON", bg=self.SUCCESS)
        else:
            self.audio_toggle_btn.config(
                text="🔇 Audio OFF", bg=self.MUTED)
        self.log(f"🎵 Niveau audio {'activé' if enabled else 'désactivé'}")

    # ── Ambilight ─────────────────────────────────────────────────────────────
    def _toggle_ambilight(self):
        if self._ambilight_active:
            self._stop_ambilight()
        else:
            self._start_ambilight()

    def _start_ambilight(self):
        missing = []
        try:
            from PIL import ImageGrab
        except ImportError:
            missing.append("Pillow")
        try:
            import numpy as np
        except ImportError:
            missing.append("numpy")
        if missing:
            messagebox.showerror("Modules manquants",
                                 f"Installez : pip install {' '.join(missing)}")
            return
        if self._immersion_active:
            self._stop_immersion()
        self._ambilight_active = True
        self.amb_btn.config(text="⏹   Arrêter Ambilight", bg=self.DANGER)
        self.log("📺 Ambilight activé")
        self._ambilight_thread = threading.Thread(
            target=self._ambilight_loop, daemon=True)
        self._ambilight_thread.start()

    def _stop_ambilight(self):
        self._ambilight_active = False
        self.amb_btn.config(text="📺   Activer Ambilight", bg=self.CARD2)
        self.log("📺 Ambilight désactivé")

    def _ambilight_loop(self):
        try:
            from PIL import ImageGrab
            import numpy as np
        except ImportError:
            return

        zones = self.amb_zones_var.get()
        prev_colors = [(0, 0, 0)] * zones

        while self._ambilight_active:
            try:
                img = ImageGrab.grab()
                W, H = img.size
                arr = np.array(img, dtype=np.float32)

                # Divise le périmètre de l'écran en N zones
                # Distribution : bas gauche → bas → droite → haut → gauche
                colors = []
                perimeter_pts = []

                # On prend des tranches de 8px sur chaque bord
                margin = 8

                # Bas (gauche → droite) : zones//4
                n_bottom = zones // 4
                for i in range(n_bottom):
                    x1 = int(W * i / n_bottom)
                    x2 = int(W * (i+1) / n_bottom)
                    strip = arr[H-margin:H, x1:x2]
                    perimeter_pts.append(strip)

                # Droite (bas → haut) : zones//4
                n_right = zones // 4
                for i in range(n_right):
                    y1 = int(H * (n_right-i-1) / n_right)
                    y2 = int(H * (n_right-i) / n_right)
                    strip = arr[y1:y2, W-margin:W]
                    perimeter_pts.append(strip)

                # Haut (droite → gauche) : zones//4
                n_top = zones // 4
                for i in range(n_top):
                    x1 = int(W * (n_top-i-1) / n_top)
                    x2 = int(W * (n_top-i) / n_top)
                    strip = arr[0:margin, x1:x2]
                    perimeter_pts.append(strip)

                # Gauche (haut → bas) : reste
                n_left = zones - n_bottom - n_right - n_top
                for i in range(n_left):
                    y1 = int(H * i / n_left)
                    y2 = int(H * (i+1) / n_left)
                    strip = arr[y1:y2, 0:margin]
                    perimeter_pts.append(strip)

                # Moyenne de chaque zone + boost saturation léger
                new_colors = []
                for strip in perimeter_pts:
                    if strip.size == 0:
                        new_colors.append((0, 0, 0))
                        continue
                    r = float(strip[:,:,0].mean())
                    g = float(strip[:,:,1].mean())
                    b = float(strip[:,:,2].mean())
                    # Boost : amplifier vers la teinte dominante
                    mx = max(r, g, b, 1)
                    r = min(255, r * (255 / mx) * 0.7 + r * 0.3)
                    g = min(255, g * (255 / mx) * 0.7 + g * 0.3)
                    b = min(255, b * (255 / mx) * 0.7 + b * 0.3)
                    new_colors.append((int(r), int(g), int(b)))

                # Lissage temporel
                if self.amb_smooth_var.get():
                    smoothed = []
                    for (r1,g1,b1), (r2,g2,b2) in zip(prev_colors, new_colors):
                        smoothed.append((
                            int(r1*0.6 + r2*0.4),
                            int(g1*0.6 + g2*0.4),
                            int(b1*0.6 + b2*0.4),
                        ))
                    new_colors = smoothed

                prev_colors = new_colors
                self._ambilight_colors = new_colors

                # Envoyer les couleurs individuelles via cmd 0x07
                packets = CommandBuilder.custom_leds(new_colors)
                for pkt in packets:
                    self.bt.send(pkt)

                # Mise à jour aperçu
                self.after(0, self._update_ambilight_preview)

            except Exception as e:
                pass
            time.sleep(1/20)  # 20 FPS

    def _update_ambilight_preview(self):
        try:
            c = self.amb_preview
            c.delete("all")
            w = c.winfo_width() or 180
            h = c.winfo_height() or 30
            colors = self._ambilight_colors
            if not colors:
                return
            seg = w / len(colors)
            for i, (r, g, b) in enumerate(colors):
                x0 = i * seg
                x1 = (i+1) * seg
                c.create_rectangle(x0, 0, x1, h,
                                   fill=f"#{r:02X}{g:02X}{b:02X}",
                                   outline="")
        except Exception:
            pass

    def _toggle_immersion(self):
        if self._immersion_active:
            self._stop_immersion()
        else:
            self._start_immersion()

    def _start_immersion(self):
        missing = []
        try:
            from PIL import ImageGrab
        except ImportError:
            missing.append("Pillow")
        try:
            import numpy as np
        except ImportError:
            missing.append("numpy")
        try:
            import sounddevice as sd
        except ImportError:
            missing.append("sounddevice")
        if missing:
            messagebox.showerror("Modules manquants",
                                 f"Installez : pip install {' '.join(missing)}")
            return
        if self._ambilight_active:
            self._stop_ambilight()
        self._immersion_active = True
        self.imm_btn.config(text="⏹   Désactiver immersion",
                            bg=self.PALETTE["danger"])
        self.log("🌌 Mode Immersion activé")
        self._immersion_thread = threading.Thread(
            target=self._immersion_loop, daemon=True)
        self._immersion_thread.start()

    def _stop_immersion(self):
        self._immersion_active = False
        self.imm_btn.config(text="🖥   Activer immersion",
                            bg=self.PALETTE["card2"])
        self.log("🌌 Mode Immersion désactivé")

    def _immersion_loop(self):
        try:
            from PIL import ImageGrab
            import numpy as np
            import sounddevice as sd
        except ImportError:
            return

        prev_r, prev_g, prev_b = 255, 0, 0
        audio_rms = 0.0
        SMOOTH = 0.25

        def _audio_cb(indata, frames, time_info, status):
            nonlocal audio_rms
            rms = float(np.sqrt(np.mean(indata ** 2)))
            audio_rms = min(1.0, rms * 12)

        try:
            stream = sd.InputStream(channels=1, samplerate=44100,
                                    blocksize=1024, callback=_audio_cb)
            stream.start()
        except Exception:
            stream = None

        while self._immersion_active:
            try:
                # ── Capture écran ──────────────────────────────────────────
                img = ImageGrab.grab()
                img_small = img.resize((64, 36))
                arr = np.array(img_small, dtype=np.float32)

                r_ch, g_ch, b_ch = arr[:,:,0], arr[:,:,1], arr[:,:,2]
                mx = np.maximum(np.maximum(r_ch, g_ch), b_ch)
                mn = np.minimum(np.minimum(r_ch, g_ch), b_ch)
                sat = np.where(mx > 0, (mx - mn) / mx, 0)
                lum = mx / 255.0
                weight = (sat ** 2) * lum
                total_w = weight.sum()

                if total_w > 0.1:
                    dom_r = float(np.sum(r_ch * weight) / total_w)
                    dom_g = float(np.sum(g_ch * weight) / total_w)
                    dom_b = float(np.sum(b_ch * weight) / total_w)
                else:
                    dom_r = float(r_ch.mean())
                    dom_g = float(g_ch.mean())
                    dom_b = float(b_ch.mean())

                # Lissage
                dom_r = prev_r * (1 - SMOOTH) + dom_r * SMOOTH
                dom_g = prev_g * (1 - SMOOTH) + dom_g * SMOOTH
                dom_b = prev_b * (1 - SMOOTH) + dom_b * SMOOTH
                prev_r, prev_g, prev_b = dom_r, dom_g, dom_b

                # Boost saturation
                dr, dg, db = dom_r/255, dom_g/255, dom_b/255
                cmax = max(dr, dg, db); cmin = min(dr, dg, db); delta = cmax - cmin
                if cmax > 0.01 and delta > 0.01:
                    if cmax == dr:   h = 60 * (((dg-db)/delta) % 6)
                    elif cmax == dg: h = 60 * (((db-dr)/delta) + 2)
                    else:            h = 60 * (((dr-dg)/delta) + 4)
                    s = min(1.0, (delta/cmax) * 1.8 + 0.25)
                    v = min(1.0, cmax * 1.3 + 0.15)
                    c2 = v * s; x2 = c2*(1 - abs((h/60)%2 - 1)); m2 = v - c2
                    hi = int(h/60) % 6
                    rm = [(c2,x2,0),(x2,c2,0),(0,c2,x2),(0,x2,c2),(x2,0,c2),(c2,0,x2)][hi]
                    dom_r = (rm[0]+m2)*255; dom_g = (rm[1]+m2)*255; dom_b = (rm[2]+m2)*255

                self._immersion_color = (
                    int(max(0, min(255, dom_r))),
                    int(max(0, min(255, dom_g))),
                    int(max(0, min(255, dom_b))),
                )

                # ── Luminosité audio ───────────────────────────────────────
                if self._audio_enabled.get():
                    bri = int(30 + min(225, audio_rms ** 0.6 * 225))
                else:
                    bri = self.brightness.get()
                    audio_rms = 0.0
                self._immersion_bri = bri
                self._audio_level = audio_rms

                # ── Envoyer ────────────────────────────────────────────────
                r2, g2, b2 = self._immersion_color
                self.bt.send(CommandBuilder.color(r2, g2, b2))
                self.bt.send(CommandBuilder.brightness(bri))

                # ── Mettre à jour l'UI ─────────────────────────────────────
                self.after(0, self._update_immersion_ui)

            except Exception:
                pass
            time.sleep(1/15)

        if stream:
            try:
                stream.stop(); stream.close()
            except Exception:
                pass

    def _update_immersion_ui(self):
        try:
            r, g, b = self._immersion_color
            hx = f"#{r:02X}{g:02X}{b:02X}"
            self.imm_swatch.config(bg=hx)
            # VU-mètre
            self.audio_bar.delete("all")
            w = self.audio_bar.winfo_width() or 120
            h = self.audio_bar.winfo_height() or 18
            fill_w = int(self._audio_level * w)
            color = ("#30D158" if self._audio_level < 0.6
                     else "#FF9F0A" if self._audio_level < 0.85
                     else "#FF453A")
            if fill_w > 0:
                self.audio_bar.create_rectangle(0, 0, fill_w, h,
                                                fill=color, outline="")
        except Exception:
            pass

    def _toggle_power(self):
        self.power_on.set(not self.power_on.get())
        on = self.power_on.get()
        bg_on  = self.SUCCESS
        bg_off = self.CARD2
        new_bg = bg_on if on else bg_off
        hover_bg = self._lighten(bg_on) if on else self.CARD3
        self.power_btn.config(
            text="⏻   Éteindre" if on else "⏻   Allumer",
            bg=new_bg,
            activebackground=hover_bg,
        )
        self.power_btn.bind("<Enter>", lambda e: self.power_btn.config(bg=hover_bg))
        self.power_btn.bind("<Leave>", lambda e: self.power_btn.config(bg=new_bg))
        self.bt.send(CommandBuilder.power(on))

    def _send_reset(self):
        self.bt.send(bytes([0xFF, 0xFE, 0xFF]))
        self.log("↺ Commande reset envoyée")

    def _send_manual(self):
        raw = self.hex_entry.get().replace(" ", "")
        try:
            data = bytes.fromhex(raw)
            self.bt.send(data)
            self.log(f"→ Manuel : {raw}")
        except ValueError:
            messagebox.showerror("Hex invalide", "Entrez des octets hex valides (ex: 01 FF 00 00 FF)")

    # ── Sélection couleur ─────────────────────────────────────────────────────
    def _pick_color(self, idx):
        init = self.color1 if idx == 1 else self.color2
        result = colorchooser.askcolor(color=init, title=f"Couleur {idx}")
        if result[1]:
            hx = result[1]
            r, g, b = self._hex_to_rgb(hx)
            if idx == 1:
                self._set_color_from_rgb(r, g, b, source="")
            else:
                self.color2 = hx
                self._send_color2()
            self.log(f"🎨 Couleur {idx} → {hx}")

    def _quick_color(self, hx):
        r, g, b = self._hex_to_rgb(hx)
        self._set_color_from_rgb(r, g, b, source="")

    def _set_effect(self, name):
        self.effect_var.set(name)
        if name == "✏ Personnalisé":
            self.open_custom_editor()
        else:
            self._send_effect()

    # ── Aperçu animé ──────────────────────────────────────────────────────────
    def _start_preview_loop(self):
        self._preview_offset = 0.0
        self._galaxy_colors = [(0, 0, 0)] * 20
        # Tableau de chaleur pour l'effet feu (valeurs 0.0-1.0 par LED)
        self._fire_heat = [0.0] * 20
        self._animate_preview()

    def _animate_preview(self):
        c = self.canvas_prev
        w = c.winfo_width() or 400
        h = 72
        n = 20
        seg = w / n
        c.delete("all")

        eff = self.effect_var.get()
        r1, g1, b1 = self._hex_to_rgb(self.color1)
        r2, g2, b2 = self._hex_to_rgb(self.color2)
        bri = self.brightness.get() / 255
        t = self._preview_offset

        for i in range(n):
            if self._immersion_active:
                r, g, b = self._immersion_color
                bri2 = self._immersion_bri / 255
                r, g, b = int(r*bri2), int(g*bri2), int(b*bri2)
                color = f"#{r:02X}{g:02X}{b:02X}"
                x0, x1 = i * seg + 2, (i + 1) * seg - 2
                self.canvas_prev.create_oval(x0, 12, x1, h-12, fill=color, outline="")
                if r+g+b > 30:
                    self.canvas_prev.create_oval(x0+3, 15, x1-3, h-15,
                                                  fill=self._lighten_hex(color, 1.4), outline="")
            elif eff == "✏ Personnalisé":
                if self.custom_led_colors:
                    idx = int(i * len(self.custom_led_colors) / n)
                    r, g, b = self.custom_led_colors[idx]
                else:
                    r, g, b = 80, 80, 80
            elif eff == "🌌 Galaxie":
                if len(self._galaxy_colors) != n:
                    self._galaxy_colors = [(0, 0, 0)] * n
                import random
                if random.random() < 0.02:
                    led_idx = random.randint(0, n - 1)
                    hue_rand = random.random() * 360
                    self._galaxy_colors[led_idx] = self._hsv_to_rgb(hue_rand, 1.0, 1.0)
                # Fade très très lent
                self._galaxy_colors = [
                    (max(0, rv - 1), max(0, gv - 1), max(0, bv - 1))
                    for rv, gv, bv in self._galaxy_colors
                ]
                r, g, b = self._galaxy_colors[i]
            elif eff == "Arc-en-ciel":
                spd_f = 0.1 + (self.speed_var.get() / 255) * 2.9
                hue = ((i / n + t * 0.005) % 1.0) * 360
                r, g, b = self._hsv_to_rgb(hue, 1, 1)
            elif eff == "Respiration":
                factor = (math.sin(t * 0.04) + 1) / 2
                r, g, b = int(r1 * factor), int(g1 * factor), int(b1 * factor)
            elif eff == "Dégradé":
                f = i / n
                r = int(r1 + (r2 - r1) * f)
                g = int(g1 + (g2 - g1) * f)
                b = int(b1 + (b2 - b1) * f)
            elif eff == "Stroboscope":
                on = (int(t * 0.25) % 2 == 0)
                r, g, b = (r1, g1, b1) if on else (0, 0, 0)
            elif eff == "Comète":
                dist = (i - (t * 0.25 % n))
                factor = max(0, 1 - abs(dist) / 4)
                r, g, b = int(r1 * factor), int(g1 * factor), int(b1 * factor)
            elif eff == "Twinkle":
                factor = (math.sin(t * 0.08 + i * 1.7) + 1) / 2
                factor = factor ** 3
                r, g, b = int(r1 * factor), int(g1 * factor), int(b1 * factor)
            elif eff == "Vague":
                f = (math.sin(i * 0.5 - t * 0.08) + 1) / 2
                r = int(r1 + (r2 - r1) * f)
                g = int(g1 + (g2 - g1) * f)
                b = int(b1 + (b2 - b1) * f)
            elif eff == "Feu":
                # Algorithme heat map identique au firmware
                # 1. Refroidissement aléatoire de chaque cellule
                if i == 0:  # ne calculer qu'une fois par frame
                    import random as _rnd
                    cooling = 0.08 + (1 - spd_raw/255) * 0.12
                    for k in range(n):
                        self._fire_heat[k] = max(0.0,
                            self._fire_heat[k] - _rnd.uniform(0, cooling))
                    # 2. Propagation chaleur de gauche à droite (convection)
                    for k in range(n-1, 1, -1):
                        self._fire_heat[k] = (
                            self._fire_heat[k-1] * 0.4 +
                            self._fire_heat[k-2] * 0.3 +
                            self._fire_heat[k]   * 0.3
                        )
                    # 3. Nouvelles flammes à la source (début du ruban)
                    if _rnd.random() < 0.6 + spd_raw/512:
                        spark_pos = _rnd.randint(0, min(2, n-1))
                        self._fire_heat[spark_pos] = min(1.0,
                            self._fire_heat[spark_pos] +
                            _rnd.uniform(0.4, 1.0))
                # Conversion chaleur → couleur feu
                heat = self._fire_heat[i]
                if heat < 0.33:
                    r = int(heat * 3 * 255)
                    g = 0; b = 0
                elif heat < 0.66:
                    r = 255
                    g = int((heat - 0.33) * 3 * 160)
                    b = 0
                else:
                    r = 255; g = 160
                    b = int((heat - 0.66) * 3 * 200)
            else:  # Statique
                r, g, b = r1, g1, b1

            r = int(r * bri)
            g = int(g * bri)
            b = int(b * bri)
            color = f"#{r:02X}{g:02X}{b:02X}"
            x0, x1 = i * seg + 2, (i + 1) * seg - 2
            c.create_oval(x0, 12, x1, h - 12, fill=color, outline="")
            if r + g + b > 30:
                c.create_oval(x0+3, 15, x1-3, h-15,
                               fill=self._lighten_hex(color, 1.4), outline="")

        # Vitesse réelle : step et délai pilotés par speed_var
        spd_raw = self.speed_var.get()
        speed_factor = 0.1 + (spd_raw / 255) * 2.9
        delay_ms = max(16, int(80 - (spd_raw / 255) * 64))
        self._preview_offset += speed_factor
        self.after(delay_ms, self._animate_preview)

    @staticmethod
    def _lighten_hex(hx, f):
        hx = hx.lstrip("#")
        r, g, b = [min(255, int(int(hx[i:i+2], 16) * f)) for i in (0, 2, 4)]
        return f"#{r:02X}{g:02X}{b:02X}"

    # ── Log ───────────────────────────────────────────────────────────────────
    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        def _write():
            self.log_text.config(state="normal")
            self.log_text.insert("end", f"[{ts}] {msg}\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.after(0, _write)

    # ── Utilitaires ───────────────────────────────────────────────────────────
    @staticmethod
    def _hex_to_rgb(hx):
        hx = hx.lstrip("#")
        return tuple(int(hx[i:i+2], 16) for i in (0, 2, 4))

    @staticmethod
    def _hsv_to_rgb(h, s, v):
        h = h / 60
        i = int(h)
        f = h - i
        p, q, t_ = v*(1-s), v*(1-s*f), v*(1-s*(1-f))
        cases = [(v,t_,p),(q,v,p),(p,v,t_),(p,q,v),(t_,p,v),(v,p,q)]
        r, g, b = cases[i % 6]
        return int(r*255), int(g*255), int(b*255)


# ─────────────────────────────────────────────────────────────────────────────
#  Firmware ESP32-S3 complet (template — les credentials WiFi sont remplacés
#  à la génération via l'onglet WiFi de l'application)
# ─────────────────────────────────────────────────────────────────────────────
ESP32_FIRMWARE_TEMPLATE = """\
// ╔══════════════════════════════════════════════════════════════════╗
// ║        LED RGB Controller — Firmware ESP32-S3                    ║
// ║        Bluetooth BLE + WiFi TCP — FastLED                        ║
// ╠══════════════════════════════════════════════════════════════════╣
// ║  Bibliothèques à installer via Arduino IDE :                     ║
// ║    • FastLED        >= 3.6   (par Daniel Garcia)                 ║
// ║    • NimBLE-Arduino >= 1.4   (par h2zero)                        ║
// ║    • WiFi + ESPmDNS (inclus avec le support ESP32)               ║
// ╠══════════════════════════════════════════════════════════════════╣
// ║  Carte : "ESP32S3 Dev Module"                                    ║
// ║  Partition : "Huge APP (3MB No OTA)"                             ║
// ╚══════════════════════════════════════════════════════════════════╝

#include <NimBLEDevice.h>
#include <FastLED.h>
#include <WiFi.h>
#include <ESPmDNS.h>

// ── Config WiFi (généré automatiquement par l'application) ──────────
const char* WIFI_SSID     = "VOTRE_SSID";
const char* WIFI_PASSWORD = "VOTRE_MOT_DE_PASSE";
const int   WIFI_TCP_PORT = 1234;

// ── Config LED ──────────────────────────────────────────────────────
#define LED_PIN        38
#define MAX_LEDS      300
#define LED_TYPE       WS2812B
#define COLOR_ORDER    GRB

int     numLeds    = 60;
CRGB    leds[MAX_LEDS];
CRGB    col1(255, 0, 0);
CRGB    col2(0, 0, 255);
uint8_t brightness = 200;
uint8_t effectId   = 0;
uint8_t spd        = 128;
bool    powerOn    = true;

// ── UUIDs BLE (Nordic UART Service) ────────────────────────────────
#define SERVICE_UUID           "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_RX "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define CHARACTERISTIC_UUID_TX "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

NimBLEServer*         pServer           = nullptr;
NimBLECharacteristic* pTxCharacteristic = nullptr;
bool                  deviceConnected   = false;

WiFiServer tcpServer(WIFI_TCP_PORT);
WiFiClient tcpClient;

// ── Traitement commandes (BLE + WiFi partagé) ───────────────────────
void processCommand(const uint8_t* v, size_t len) {
  if (len < 2) return;
  switch (v[0]) {
    case 0x01: if (len>=4) col1=CRGB(v[1],v[2],v[3]); break;
    case 0x02: brightness=v[1]; FastLED.setBrightness(brightness); break;
    case 0x03: if (len>=3) numLeds=min((int)((v[1]<<8)|v[2]),MAX_LEDS); break;
    case 0x04: effectId=v[1]; if(len>=3) spd=v[2]; break;
    case 0x05: if (len>=4) col2=CRGB(v[1],v[2],v[3]); break;
    case 0x06: powerOn=(v[1]==0x01); if(!powerOn) FastLED.clear(true); break;
    case 0x07: {
      if (len<6) break;
      int start=(v[1]<<8)|v[2], count=min((int)((v[3]<<8)|v[4]),(int)((len-6)/3));
      for(int i=0;i<count;i++){int idx=start+i;if(idx<MAX_LEDS)leds[idx]=CRGB(v[5+i*3],v[5+i*3+1],v[5+i*3+2]);}
      FastLED.show(); break;
    }
    case 0xF0: if(tcpClient&&tcpClient.connected()) tcpClient.write(0xF1); break;
    case 0xFF: delay(100); ESP.restart(); break;
  }
}

// ── Callbacks BLE ───────────────────────────────────────────────────
class MyServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* s) override { deviceConnected=true; Serial.println("[BLE] Connecté"); }
  void onDisconnect(NimBLEServer* s) override { deviceConnected=false; NimBLEDevice::startAdvertising(); Serial.println("[BLE] Déconnecté"); }
};
class MyRxCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* c) override { std::string v=c->getValue(); processCommand((const uint8_t*)v.data(),v.size()); }
};

// ── Effets ──────────────────────────────────────────────────────────
uint8_t hue = 0;
void runEffect() {
  if (!powerOn) return;
  switch (effectId) {
    case 0:  fill_solid(leds, numLeds, col1); break;
    case 1:  fill_gradient_RGB(leds, numLeds, col1, col2); break;
    case 2:  fill_rainbow(leds, numLeds, hue, 255/max(numLeds,1)); hue+=(spd/32)+1; break;
    case 3:  { uint8_t b=beatsin8(spd/4+1,10,255); fill_solid(leds,numLeds,col1); FastLED.setBrightness(b); } break;
    case 4:  { bool on=(millis()/max(1U,(uint32_t)(256-spd)))%2; fill_solid(leds,numLeds,on?col1:CRGB::Black); } break;
    case 5:  for(int i=numLeds-1;i>0;i--) leds[i]=leds[i-1]; leds[0]=CHSV(random8(20),200+random8(55),200+random8(55)); break;
    case 6:  for(int i=0;i<numLeds;i++){uint8_t b=beatsin8(spd/8+1,0,255,0,(uint8_t)(i*256/max(numLeds,1)));leds[i]=blend(col1,col2,b);} break;
    case 7:  { uint8_t pos=beatsin8(spd/8+1,0,numLeds-1); fadeToBlackBy(leds,numLeds,20); leds[pos]=col1; } break;
    case 8:  for(int i=0;i<numLeds;i++){if(random8()<spd/4)leds[i]=col1;else leds[i].fadeToBlackBy(40);} break;
    case 9:  { bool on=(millis()/100)%2; fill_solid(leds,numLeds,on?CRGB::Blue:CRGB::Red); } break;
    case 10: break; // Personnalisé — écrit via cmd 0x07
    case 11: { // Galaxie — twinkle couleurs aléatoires, extinction très lente
      fadeToBlackBy(leds, numLeds, 1 + spd/128);
      int stars=max(1,(int)(numLeds*spd/12000));
      for(int s=0;s<stars;s++) leds[random16(numLeds)]=CHSV(random8(),255,255);
    } break;
  }
}

// ── Setup ────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println("\\n[ESP32-S3] LED Controller BLE+WiFi");

  FastLED.addLeds<LED_TYPE,LED_PIN,COLOR_ORDER>(leds,MAX_LEDS).setCorrection(TypicalLEDStrip);
  FastLED.setBrightness(brightness);
  FastLED.clear(true);

  // WiFi
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[WiFi] Connexion à %s", WIFI_SSID);
  unsigned long t0=millis();
  while(WiFi.status()!=WL_CONNECTED && millis()-t0<10000){delay(300);Serial.print(".");}
  if(WiFi.status()==WL_CONNECTED){
    Serial.printf("\\n[WiFi] IP : %s\\n", WiFi.localIP().toString().c_str());
    tcpServer.begin();
    if(MDNS.begin("esp32-leds")) MDNS.addService("led-ctrl","tcp",WIFI_TCP_PORT);
    Serial.printf("[WiFi] TCP port %d | mDNS : esp32-leds.local\\n", WIFI_TCP_PORT);
  } else {
    Serial.println("\\n[WiFi] Echec — mode BLE uniquement");
  }

  // BLE
  NimBLEDevice::init("ESP32-S3 LEDs");
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);
  pServer=NimBLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());
  auto* svc=pServer->createService(SERVICE_UUID);
  pTxCharacteristic=svc->createCharacteristic(CHARACTERISTIC_UUID_TX,NIMBLE_PROPERTY::NOTIFY);
  auto* rx=svc->createCharacteristic(CHARACTERISTIC_UUID_RX,NIMBLE_PROPERTY::WRITE|NIMBLE_PROPERTY::WRITE_NR);
  rx->setCallbacks(new MyRxCallbacks());
  svc->start();
  NimBLEDevice::getAdvertising()->addServiceUUID(SERVICE_UUID);
  NimBLEDevice::startAdvertising();
  Serial.println("[BLE] Advertising — \\"ESP32-S3 LEDs\\"");
}

// ── Loop ─────────────────────────────────────────────────────────────
void loop() {
  // WiFi TCP
  if(WiFi.status()==WL_CONNECTED){
    if(!tcpClient||!tcpClient.connected()) tcpClient=tcpServer.accept();
    if(tcpClient&&tcpClient.connected()&&tcpClient.available()){
      static uint8_t buf[512]; static int bufLen=0;
      while(tcpClient.available()){
        uint8_t b=tcpClient.read();
        if(bufLen<(int)sizeof(buf)) buf[bufLen++]=b;
        if(b==0xFF){processCommand(buf,bufLen);bufLen=0;}
      }
    }
  }
  if(!powerOn){delay(50);return;}
  runEffect();
  FastLED.show();
  delay(max(5,(256-(int)spd)/8));
}
"""

# Alias pour la visionneuse (menu Outils)
ESP32_FIRMWARE = ESP32_FIRMWARE_TEMPLATE


def show_firmware():
    win = tk.Toplevel()
    win.title("Firmware ESP32-S3")
    win.geometry("700x600")
    win.configure(bg="#0D0D14")
    tk.Label(win, text="Code Arduino à flasher sur l'ESP32-S3",
             font=("Consolas", 12, "bold"),
             bg="#0D0D14", fg="#7C3AED").pack(pady=10)
    txt = tk.Text(win, bg="#09090F", fg="#A5F3FC",
                  font=("Consolas", 9), relief="flat")
    txt.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    txt.insert("1.0", ESP32_FIRMWARE.strip())
    txt.config(state="disabled")
    sb = tk.Scrollbar(win, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)


if __name__ == "__main__":
    app = LEDControllerApp()
    # Menu
    menubar = tk.Menu(app, bg="#13131F", fg="#E2E8F0",
                      activebackground="#7C3AED", tearoff=0)
    tools = tk.Menu(menubar, bg="#13131F", fg="#E2E8F0",
                    activebackground="#7C3AED", tearoff=0)
    tools.add_command(label="Voir le firmware ESP32", command=show_firmware)
    tools.add_separator()
    tools.add_command(label="Quitter", command=app.destroy)
    menubar.add_cascade(label="Outils", menu=tools)
    app.config(menu=menubar)
    app.mainloop()
print("good")