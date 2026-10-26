"""
Mochi Break — Pomodoro timer dengan maskot kucing "Mochi".

Jalankan:
    python mochi_break.py

Dependensi opsional:
    pip install Pillow   # untuk gambar WebP/JPG custom

Fitur:
    - Timer kerja / istirahat (Pomodoro)
    - Overlay full-screen saat istirahat dengan Mochi
    - Mochi berjalan masuk dari samping layar, duduk di tengah
    - Desktop pet yang bisa jalan, di-drag, dan diklik
    - Dukungan gambar kucing custom (PNG/GIF/WebP/JPG)
"""
import bisect
import itertools
import json
import math
import os
import random
import struct
import tempfile
import threading
import time
import tkinter as tk
import wave
from tkinter import ttk, filedialog, messagebox

try:
    import winsound
except ImportError:
    winsound = None

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

APP_NAME = "Mochi Break"
CONFIG = os.path.join(os.path.expanduser("~"), ".cat_gatekeeper.json")
DEFAULT = {"work": 25, "brk": 5, "auto": True, "full": False, "bg": False, "foto": "", "size": 60}
KEY = "#010101"

# Tinggi kucing bawaan (MochiRig) dalam satuan skala, dari ujung telinga ke ujung kaki.
CAT_HEIGHT_UNITS = 100
# Kecepatan jalan desktop pet, piksel per detik.
PET_SPEED_PX = 27.0
# Durasi walk-in Mochi ke tengah layar, dalam detik.
ENTRANCE_SECONDS = 2.0

BG = "#F1F5F9"
CARD_BG = "#FFFFFF"
ORANGE = "#F97316"
TEXT = "#1E293B"
TEXT_MUTED = "#64748B"


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
def _bounded_int(value, fallback, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def load_cfg():
    """Load user preferences, safely falling back to defaults."""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return dict(DEFAULT)
        cfg = {**DEFAULT, **raw}
        cfg["work"] = _bounded_int(cfg.get("work"), 25, 1, 180)
        cfg["brk"] = _bounded_int(cfg.get("brk"), 5, 1, 60)
        cfg["size"] = _bounded_int(cfg.get("size"), 60, 20, 95)
        for key in ("auto", "full", "bg"):
            cfg[key] = bool(cfg.get(key, DEFAULT[key]))
        cfg["foto"] = cfg.get("foto") if isinstance(cfg.get("foto"), str) else ""
        return cfg
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return dict(DEFAULT)


def save_cfg(cfg):
    """Write settings atomically so a partial write is less likely."""
    folder = os.path.dirname(CONFIG) or "."
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=folder, delete=False
        ) as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            tmp_path = f.name
        os.replace(tmp_path, CONFIG)
        return True
    except OSError as exc:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        messagebox.showwarning(APP_NAME, f"Pengaturan tidak bisa disimpan.\n\n{exc}")
        return False


def fmt(sec):
    sec = max(int(sec), 0)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def fmt_short(sec):
    sec = max(int(sec), 0)
    return f"{sec // 60}:{sec % 60:02d}"


def _beep_async():
    """Beep dua nada di thread terpisah supaya Tk tidak freeze."""
    if winsound is None:
        return
    try:
        winsound.Beep(880, 200)
        winsound.Beep(660, 300)
    except RuntimeError:
        pass


def rrect(c, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        x1 + r, y1
    ]
    return c.create_polygon(pts, smooth=True, **kw)


SR = 22050
SND_DIR = os.path.join(tempfile.gettempdir(), "mochi_break_sounds")


def _synth(path, dur, fn):
    frames = bytearray()
    for i in range(int(SR * dur)):
        v = max(-1.0, min(1.0, fn(i / SR, dur)))
        frames += struct.pack("<h", int(32767 * v))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))


def _meow_fn():
    ph = [0.0]

    def f(t, d):
        u = t / d
        freq = 380 + 420 * math.sin(math.pi * u) ** 0.8 - 80 * u
        ph[0] += 2 * math.pi * freq / SR
        env = (math.sin(math.pi * u) ** 0.6) * 0.5
        form = 700 + 1300 * math.sin(math.pi * u)
        s = sum(math.sin(k * ph[0]) * math.exp(-((freq * k - form) / 600) ** 2)
                / math.sqrt(k) for k in range(1, 13))
        return env * s * 1.4
    return f


def _purr_fn():
    def f(t, d):
        am = 0.55 + 0.45 * math.sin(2 * math.pi * 25 * t)
        s = sum(math.sin(2 * math.pi * 85 * k * t) / k for k in (1, 2, 3, 4))
        return 0.4 * am * s * min(1.0, t / 0.2, (d - t) / 0.3)
    return f


# ──────────────────────────────────────────────────────────────────────────────
# MochiRig — kucing HD + walk-in
# ──────────────────────────────────────────────────────────────────────────────
class MochiRig:
    """Mochi digambar SEKALI; tiap frame hanya koordinat bagian bergerak yang diperbarui.

    Fitur HD: fur tufts, highlight kepala, 3 kilau mata, outline mata, kumis melengkung.
    Fitur interaktif: walk-in dari samping (entrance_offset), langkah kaki bergantian,
    happy-hop saat tiba, hover menajamkan pandangan, kedip, menguap, wave, poke."""

    OUT, ORANGE, DARK = "#5B463B", "#F6A34A", "#49413F"
    CREAM, CREAM_LT = "#FFF7EB", "#FFFDF8"
    PINK, BLUSH = "#F4A6A8", "#F9B4BC"
    EYE_DARK = "#2A2320"

    def __init__(self, c, cx, cy, s, tag, bubble=False,
                 entrance_offset=0.0, walk_speed=0.0):
        self.c, self.cx, self.cy, self.s = c, cx, cy, s
        self.tag, self.grp = tag, tag + "_grp"
        self.dy = 0.0
        self.wag_amp = self.tail_phase = 0.0
        self.lx = self.ly = self.tilt = 0.0
        self.blush = 1.0
        self.wave_amt = 0.0
        self.t_start = self.last_t = None
        self.eff = None
        self.happy_until, self.happy_start = -1.0, -99.0
        self.poke_req = False
        self.yawn_start, self.next_yawn = -99.0, 15.0
        self.bub_msg = None
        self.vis = {}
        # Entrance / walk
        self.walk_offset = entrance_offset
        # Item statis dibangun di cx; geser pertama ke cx + walk_offset.
        self.prev_walk_offset = 0.0
        self.walk_speed = walk_speed
        self.walking = entrance_offset > 0 and walk_speed > 0
        self.walk_phase = 0.0
        # Hover
        self.hover = 0.0
        self.hover_target = 0.0
        self._build(bubble)

    # ---------- util ----------
    def set_hover(self, on):
        self.hover_target = 1.0 if on else 0.0

    def _show(self, item, on):
        if self.vis.get(item) != on:
            self.vis[item] = on
            self.c.itemconfig(item, state="normal" if on else "hidden")

    def poke(self):
        """Dipanggil saat kucing diklik: senang + hati melayang."""
        self.poke_req = True

    def _tail(self, cx, y, w1, w2):
        c, s = self.c, self.s
        c.coords(self.tail1, cx + 28 * s, y + 38 * s, cx + 53 * s, y + 28 * s,
                 cx + 58 * s + w1, y + 3 * s, cx + 45 * s + w2, y - 8 * s)
        c.coords(self.tail2, cx + 49 * s + (w1 + w2) / 2, y + 7 * s,
                 cx + 45 * s + w2, y - 6 * s)

    # ---------- bangun semua item sekali ----------
    def _build(self, bubble):
        c, cx, y, s = self.c, self.cx, self.cy, self.s
        OUT, ORANGE, DARK = self.OUT, self.ORANGE, self.DARK
        CREAM, CREAM_LT, PINK, BLUSH = self.CREAM, self.CREAM_LT, self.PINK, self.BLUSH
        T, G = (self.tag,), (self.tag, self.grp)
        xy = lambda v: v * s
        ow = lambda v: max(1, int(xy(v)))
        self.hpolys, self.hboxes, self.fboxes = [], [], []
        self.whiskers, self.eyes, self.blush_ids, self.mouth_arcs = [], [], [], []

        def A(*pairs):
            out = []
            for dx, dy in pairs:
                out += [cx + dx * s, y + dy * s]
            return out

        def B(x1, y1, x2, y2):
            return (cx + x1 * s, y + y1 * s, cx + x2 * s, y + y2 * s)

        def hpoly(pairs, tw=0.0, **kw):
            pts = A(*pairs)
            pid = c.create_polygon(*pts, tags=T, **kw)
            self.hpolys.append((pid, pts, tw))
            return pid

        # ekor (posisi sementara off-screen; update() yang menaruh)
        self.tail1 = c.create_line(-9999, -9999, -9999, -9999, -9999, -9999,
                                   -9999, -9999, smooth=True,
                                   width=max(2, int(xy(11))),
                                   fill=ORANGE, capstyle="round", tags=T)
        self.tail2 = c.create_line(-9999, -9999, -9999, -9999, smooth=True,
                                   width=ow(3), fill=DARK, capstyle="round", tags=T)

        # badan
        c.create_oval(*B(-40, -2, 40, 61), fill=CREAM, outline=OUT, width=ow(2), tags=G)
        c.create_oval(*B(-33, 3, -7, 28), fill=ORANGE, outline="", tags=G)
        c.create_oval(*B(10, 13, 34, 40), fill=DARK, outline="", tags=G)
        c.create_oval(*B(-19, 22, 20, 57), fill=CREAM, outline="", tags=G)
        # chest fur tuft (HD)
        c.create_polygon(*A((-8, 22), (-5, 17), (-1, 22), (3, 16), (7, 21)),
                         fill=CREAM, outline="", smooth=True, tags=G)
        # paw kiri & kanan standalone (bisa dianimasikan)
        self.paw_l = c.create_oval(*B(-31, 45, -7, 62), fill=CREAM, outline=OUT,
                                   width=ow(1.5), tags=T)
        self.paw_r = c.create_oval(*B(7, 45, 31, 62), fill=CREAM, outline=OUT,
                                   width=ow(1.5), tags=T)
        self.paw_l_rel = (-31, 45, -7, 62)
        self.paw_r_rel = (7, 45, 31, 62)
        self.arm_out = c.create_line(0, 0, 1, 1, width=max(2, int(xy(11))) + 2 * ow(2),
                                     fill=OUT, capstyle="round", state="hidden", tags=T)
        self.arm_in = c.create_line(0, 0, 1, 1, width=max(2, int(xy(11))),
                                    fill=CREAM, capstyle="round", state="hidden", tags=T)

        # telinga + fur tufts di kepala (HD)
        hpoly([(-34, 1), (-30, -38), (-5, -16)], fill=ORANGE, outline=OUT, width=ow(2))
        hpoly([(5, -16), (30, -39), (36, 1)], tw=1.0, fill=DARK, outline=OUT, width=ow(2))
        hpoly([(-27, -7), (-28, -29), (-13, -15)], fill=PINK, outline="")
        hpoly([(13, -15), (28, -30), (29, -7)], tw=0.8, fill=PINK, outline="")
        # 3 fur tufts di atas kepala (HD)
        for tuft in ([(-4, -27), (0, -34), (4, -27)],
                     [(-18, -25), (-15, -32), (-11, -23)],
                     [(11, -23), (15, -32), (18, -25)]):
            hpoly(tuft, fill=CREAM, outline=OUT, width=ow(1.5))

        # kepala: 32-titik (HD — lebih halus dari 28)
        ell = [(39 * math.cos(a * math.tau / 32), -0.5 + 27.5 * math.sin(a * math.tau / 32))
               for a in range(32)]
        hpoly(ell, smooth=True, fill=CREAM, outline=OUT, width=ow(2))
        # highlight kepala (HD — ellipse poligon agar ikut rotasi)
        hl = [(14 * math.cos(k * math.tau / 16) - 8,
               -14.5 + 5 * math.sin(k * math.tau / 16))
              for k in range(16)]
        hpoly(hl, smooth=True, fill=CREAM_LT, outline="")
        for box_, col in (((-32, -25, -8, -2), ORANGE), ((8, -26, 29, -5), DARK)):
            b = B(*box_)
            self.hboxes.append((c.create_oval(*b, fill=col, outline="", tags=T), b))
        hpoly([(-8, -26), (2, -12), (11, -26)], fill=ORANGE, outline="")

        # pipi merona
        for dxb in (-27, 27):
            self.blush_ids.append((c.create_oval(0, 0, 1, 1, fill=BLUSH, outline="",
                                                 tags=T), dxb))

        # mata: 3 highlight + outline (HD)
        for exd in (-17, 17):
            white = c.create_oval(0, 0, 1, 1, fill="#FFFFFF", outline=OUT,
                                  width=ow(1.5), tags=T)
            line = c.create_line(0, 0, 1, 1, 2, 2, smooth=True, width=ow(2), fill=OUT,
                                 capstyle="round", state="hidden", tags=T)
            hap = c.create_line(0, 0, 1, 1, 2, 2, smooth=True, width=ow(2.2), fill=OUT,
                                capstyle="round", state="hidden", tags=T)
            pupil = c.create_oval(0, 0, 1, 1, fill=self.EYE_DARK, outline="", tags=T)
            sh1 = c.create_oval(0, 0, 1, 1, fill="white", outline="", tags=T)
            sh2 = c.create_oval(0, 0, 1, 1, fill="white", outline="", tags=T)
            sh3 = c.create_oval(0, 0, 1, 1, fill="white", outline="", tags=T)
            self.eyes.append((exd, white, line, hap, pupil, sh1, sh2, sh3))

        # hidung & mulut
        b = B(-3, 9, 3, 13)
        self.fboxes.append((c.create_oval(*b, fill=PINK, outline="", tags=T), b))
        for box_, st in (((-9, 11, 0, 21), 180), ((0, 11, 9, 21), 200)):
            b = B(*box_)
            item = c.create_arc(*b, start=st, extent=160, style="arc", outline=OUT,
                                width=ow(1.5), tags=T)
            self.fboxes.append((item, b))
            self.mouth_arcs.append(item)
        self.ymouth = c.create_oval(0, 0, 1, 1, fill="#7A2E3C", outline=OUT,
                                    width=ow(1.2), state="hidden", tags=T)
        self.tongue = c.create_oval(0, 0, 1, 1, fill="#F08A9A", outline="",
                                    state="hidden", tags=T)

        # kumis melengkung (HD)
        for side in (-1, 1):
            for k in (-1, 0, 1):
                pts = A((side * 33, 9 + k * 3.5),
                        (side * 43, 8 + k * 5.5),
                        (side * 53, 6 + k * 8))
                item = c.create_line(*pts, fill=OUT, width=ow(1.2),
                                     capstyle="round", smooth=True, tags=T)
                self.whiskers.append((item, pts))

        # kalung
        c.create_arc(*B(-29, 27, 29, 43), start=5, extent=170, style="arc",
                     outline="#D59A24", width=ow(4), tags=G)
        c.create_oval(*B(-4, 38, 4, 46), fill="#F4C64E", outline="#B78118",
                      width=1, tags=G)

        # hati & "z z"
        hf = ("Segoe UI", -max(8, int(xy(15))), "bold")
        self.hearts = [(c.create_text(0, 0, text="\u2665", fill="#F47286", font=hf,
                                      state="hidden", tags=T), dx)
                       for dx in (-12, 10, 30)]
        self.zz = c.create_text(cx + xy(39), y - xy(42), text="z z", fill="#64748B",
                                font=("Segoe UI", -max(7, int(xy(13))), "bold"),
                                state="hidden", tags=G)

        # gelembung ucapan (khusus overlay)
        self.bub = None
        if bubble:
            x1, x2, y1, y2 = cx - 108 * s, cx - 54 * s, y - 24 * s, y
            g = dict(state="hidden", tags=G)
            tip = (cx - 44 * s, y - 9 * s)
            b1 = rrect(c, x1, y1, x2, y2, 8 * s, fill="white", outline=OUT,
                       width=ow(1.5), **g)
            b2 = c.create_polygon(x2 - ow(1.5), y - 15 * s, x2 - ow(1.5), y - 5 * s,
                                  *tip, fill="white", outline="", **g)
            b3 = c.create_line(x2, y - 15 * s, *tip, x2, y - 5 * s, fill=OUT,
                               width=ow(1.5), joinstyle="round", **g)
            b4 = c.create_text((x1 + x2) / 2, (y1 + y2) / 2, text="Miawww...",
                               fill=OUT, font=("Segoe UI", -max(8, int(xy(11))), "bold"),
                               **g)
            self.bub = (b1, b2, b3, b4)

        # geser seluruh grup untuk posisi awal walk-in
        if self.walking:
            c.move(self.tag, self.walk_offset, 0)
            self.prev_walk_offset = self.walk_offset

    # ---------- per frame ----------
    def update(self, t, look, mood, hover=False):
        c, s = self.c, self.s
        if self.t_start is None:
            self.t_start = self.last_t = t
        dt = max(0.0, min(t - self.last_t, 0.2))
        self.last_t = t
        rel = t - self.t_start

        # --- walk-in state machine ---
        if self.walking:
            self.walk_offset = max(0.0, self.walk_offset - self.walk_speed * dt)
            self.walk_phase += dt * 11.0
            if self.walk_offset <= 0.0:
                self.walk_offset = 0.0
                self.walking = False
                self.happy_until = t + 1.1
                self.happy_start = t
            mood = "walk"

        # geser horizontal (hanya jika ada perubahan)
        delta_x = self.walk_offset - self.prev_walk_offset
        if delta_x:
            c.move(self.tag, delta_x, 0)
            self.prev_walk_offset = self.walk_offset
        cx = self.cx + self.walk_offset
        cyb = self.cy

        # hover
        self.hover += ((1.0 if hover else 0.0) - self.hover) * 0.15

        if self.poke_req:
            self.poke_req = False
            self.happy_until = t + 1.8
        eff = "happy" if t < self.happy_until else mood
        if eff == "happy" and self.eff != "happy":
            self.happy_start = t
        self.eff = eff
        sleeping, waving, happy = eff == "sleep", eff == "wave", eff == "happy"

        # menguap hanya saat idle
        if eff == "idle" and rel >= self.next_yawn and t - self.yawn_start > 2.4:
            self.yawn_start = t
            self.next_yawn = rel + random.uniform(18.0, 28.0)
        yu = (t - self.yawn_start) / 2.4
        e = math.sin(math.pi * yu) if (0.0 <= yu <= 1.0 and eff == "idle") else 0.0
        yawning = e > 0.02

        # napas + walk bob
        bob = 0.0 if sleeping else 2.0 * math.sin(t * 3.2) * s
        if self.walking:
            bob += abs(math.sin(self.walk_phase)) * 2.5 * s
        bob += 3.0 * s * e
        hu = t - self.happy_start
        if happy and hu < 1.2:
            bob -= abs(math.sin(hu * math.pi * 3)) * 5.0 * s * (1 - hu / 1.2)
        c.move(self.grp, 0, bob - self.dy)
        self.dy = bob
        off = bob - 4.0 * s * e

        # ekor
        if sleeping:
            target = 0.0
        elif happy:
            target = 1.3
        elif self.walking:
            target = 1.5
        elif yawning:
            target = 0.4
        else:
            target = 1.0
        self.wag_amp += (target - self.wag_amp) * 0.12
        self.tail_phase += dt * (8.0 if happy else (6.0 if self.walking else 4.2))
        w1 = self.wag_amp * math.sin(self.tail_phase) * 9 * s
        w2 = self.wag_amp * math.sin(self.tail_phase - 0.8) * 13 * s
        self._tail(cx, cyb + bob, w1, w2)

        # kepala
        lk = (0.0, 0.0) if sleeping else look
        self.lx += (lk[0] - self.lx) * 0.2
        self.ly += (lk[1] - self.ly) * 0.2
        th_t = self.lx * 0.12 - 0.07 * e
        if happy:
            th_t += 0.07 * math.sin(t * 9)
        if sleeping:
            th_t = 0.12
        self.tilt += (th_t - self.tilt) * 0.15
        cs, sn = math.cos(self.tilt), math.sin(self.tilt)
        px, py = cx, cyb + 24 * s
        fx, fy = self.lx * 3 * s, self.ly * 1.8 * s

        def H(x, y):
            ddx, ddy = x - px, y - py
            return px + ddx * cs - ddy * sn, py + ddx * sn + ddy * cs + off

        def F(x, y):
            return H(x + fx, y + fy)

        def flat(pts, f):
            out = []
            for i in range(0, len(pts), 2):
                out.extend(f(pts[i], pts[i + 1]))
            return out

        def box(item, x1, y1, x2, y2, f):
            mx, my = f((x1 + x2) / 2, (y1 + y2) / 2)
            hw, hh = (x2 - x1) / 2, (y2 - y1) / 2
            c.coords(item, mx - hw, my - hh, mx + hw, my + hh)

        # Koordinat tersimpan dibangun di self.cx; tambahkan walk_offset.
        wo = self.walk_offset

        def Hs(x, y):
            return H(x + wo, y)

        def Fs(x, y):
            return F(x + wo, y)

        tw = 3 * s * math.sin(t * 45) if (t % 7.0) > 6.6 else 0.0
        for pid, pts, w in self.hpolys:
            if w:
                pts = list(pts)
                pts[2] += tw * w
            c.coords(pid, *flat(pts, Hs))
        for item, b in self.hboxes:
            box(item, *b, Hs)
        for item, b in self.fboxes:
            box(item, *b, Fs)

        # pipi merona
        self.blush += ((1.3 if happy else 1.0) - self.blush) * 0.2
        for item, dxb in self.blush_ids:
            bx_, by_ = F(cx + dxb * s, cyb + 12 * s)
            c.coords(item, bx_ - 7 * s * self.blush, by_ - 3.8 * s * self.blush,
                     bx_ + 7 * s * self.blush, by_ + 3.8 * s * self.blush)

        # mata
        op = 1.0
        if happy or waving:
            mode = "happy"
        elif sleeping or yawning:
            mode = "closed"
        else:
            p = (t % 5.0) - 4.72
            op = 1.0 - math.sin(math.pi * p / 0.28) if 0 <= p < 0.28 else 1.0
            mode = "closed" if op < 0.18 else "open"
        pupil_dilate = 1.0 + 0.15 * self.hover
        for exd, white, line, hap, pupil, sh1, sh2, sh3 in self.eyes:
            ex = cx + exd * s
            for it in (white, pupil, sh1, sh2, sh3):
                self._show(it, mode == "open")
            self._show(line, mode == "closed")
            self._show(hap, mode == "happy")
            if mode == "open":
                mx, my = F(ex, cyb + 1.5 * s)
                hh = 7.5 * s * op
                c.coords(white, mx - 7 * s, my - hh, mx + 7 * s, my + hh)
                qx, qy = F(ex + self.lx * 2 * s, cyb + 2 * s + self.ly * 1.2 * s)
                pw = 5 * s * pupil_dilate
                ph = 6.2 * s * op * pupil_dilate
                c.coords(pupil, qx - pw, qy - ph, qx + pw, qy + ph)
                c.coords(sh1, qx - 3.6 * s, qy - 4.3 * s * op,
                         qx - 0.2 * s, qy - 0.9 * s * op)
                c.coords(sh2, qx + 1.4 * s, qy + 1.9 * s * op,
                         qx + 3.2 * s, qy + 3.7 * s * op)
                c.coords(sh3, qx - 1.2 * s, qy + 0.3 * s * op,
                         qx + 1.2 * s, qy + 2.2 * s * op)
            elif mode == "closed":
                c.coords(line, *flat([ex - 5 * s, cyb + 1 * s, ex, cyb + 3 * s,
                                      ex + 5 * s, cyb + 1 * s], F))
            else:
                c.coords(hap, *flat([ex - 5.5 * s, cyb + 3.5 * s, ex, cyb - 2.5 * s,
                                     ex + 5.5 * s, cyb + 3.5 * s], F))

        # mulut menguap
        for arc in self.mouth_arcs:
            self._show(arc, not yawning)
        self._show(self.ymouth, yawning)
        self._show(self.tongue, yawning)
        if yawning:
            mx, top = F(cx, cyb + 14 * s)
            hw, hh = (3 + 3 * e) * s, (1 + 8 * e) * s
            c.coords(self.ymouth, mx - hw, top, mx + hw, top + 2 * hh)
            c.coords(self.tongue, mx - hw * 0.6, top + hh * 1.2,
                     mx + hw * 0.6, top + hh * 1.9)

        for item, pts in self.whiskers:
            c.coords(item, *flat(pts, Fs))

        # lengan melambai
        self.wave_amt += ((1.0 if waving else 0.0) - self.wave_amt) * 0.15
        a = self.wave_amt
        arm_on = a > 0.03
        self._show(self.arm_out, arm_on)
        self._show(self.arm_in, arm_on)
        if arm_on:
            pxw = cx + 19 * s + 27 * s * a + math.sin(t * 10) * 6 * s * a
            pyw = cyb + 53.5 * s - 49.5 * s * a + bob
            c.coords(self.paw_r, pxw - 12 * s, pyw - 8.5 * s,
                     pxw + 12 * s, pyw + 8.5 * s)
            sx, sy = cx + 27 * s, cyb + 42 * s + bob
            c.coords(self.arm_out, sx, sy, pxw, pyw)
            c.coords(self.arm_in, sx, sy, pxw, pyw)

        # paw kiri & kanan (dengan animasi langkah)
        lp_dy = rp_dy = 0.0
        if self.walking:
            step = math.sin(self.walk_phase)
            lp_dy = max(0.0, step) * 4.0 * s
            rp_dy = max(0.0, -step) * 4.0 * s
        for item, prel, dy_ in ((self.paw_l, self.paw_l_rel, lp_dy),
                                (self.paw_r, self.paw_r_rel, rp_dy)):
            if item is self.paw_r and arm_on:
                continue  # paw_r sudah diposisikan oleh wave
            x1, y1, x2, y2 = prel
            c.coords(item,
                     cx + x1 * s, cyb + y1 * s + bob + dy_,
                     cx + x2 * s, cyb + y2 * s + bob + dy_)

        # hati melayang
        for i, (hid, hdx) in enumerate(self.hearts):
            u = (hu - i * 0.35) / 1.3
            on = 0 <= u < 1
            self._show(hid, on)
            if on:
                c.coords(hid, cx + (hdx + 3 * math.sin(u * 6 + i)) * s,
                         cyb - (32 + 22 * u) * s + bob)
        self._show(self.zz, sleeping)

        # gelembung ucapan
        if self.bub:
            on = waving or 0.4 <= rel <= 3.4
            for it in self.bub:
                self._show(it, on)
            msg = "dadah~" if waving else "Miawww..."
            if msg != self.bub_msg:
                self.bub_msg = msg
                c.itemconfig(self.bub[3], text=msg)


# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────
class App:
    def __init__(self):
        self.cfg = load_cfg()
        self.mode = "idle"
        self.remaining = 0
        self.paused = False
        self.deadline = None
        self.pause_started = None

        self.ov = self.ov_canvas = self.ov_text = None
        self.blocker = None
        self.frames, self.delays, self.fi = [], [], 0
        self.cache = None
        self.anim_id = None
        self.fade_id = None
        self.close_id = None
        self.t = 0.0
        self.cat_item = None
        self.cat_geom = (0, 0, 1)
        self.closing = False
        self._closing_app = False
        self._ov_alpha_target = 1.0
        self._entrance_offset = 0.0
        self._entrance_speed = 0.0

        # Desktop pet state.
        self.pet = None
        self.pet_canvas = None
        self.pet_anim_id = None
        self.pet_x = 0
        self.pet_y = 0
        self.pet_dx = PET_SPEED_PX
        self.pet_walk = True
        self.pet_sleeping = False
        self.pet_happy_until = 0.0
        self.pet_drag = None
        self._pet_was_walking = False
        self.pet_t0 = 0.0
        self.pet_last = 0.0
        self.pet_n = 0
        self.pet_rig = None
        self.rig = None
        self.t0 = 0.0
        self.cum = []
        self._lift_n = 0

        r = self.root = tk.Tk()
        r.title(APP_NAME)
        r.configure(bg=BG)
        r.geometry("360x690")
        r.resizable(False, False)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD_BG)
        style.configure("TCheckbutton", background=CARD_BG, foreground=TEXT,
                        font=("Segoe UI", 9))
        style.configure("Card.TLabel", background=CARD_BG, foreground=TEXT,
                        font=("Segoe UI", 9))

        self.timer = tk.Label(r, text="25:00", font=("Segoe UI Black", 54),
                              bg=BG, fg=TEXT)
        self.timer.pack(pady=(22, 0))
        self.status = tk.Label(r, text="Siap", font=("Segoe UI", 12),
                               bg=BG, fg=TEXT_MUTED)
        self.status.pack()

        bf = tk.Frame(r, bg=BG)
        bf.pack(pady=16)

        def make_btn(parent, text, cmd, bg_col, fg_col):
            return tk.Button(
                parent, text=text, width=8, bg=bg_col, fg=fg_col,
                relief="flat", font=("Segoe UI", 10, "bold"),
                cursor="hand2", command=cmd
            )

        self.b_start = make_btn(bf, "Start", self.start, ORANGE, "white")
        self.b_pause = make_btn(bf, "Pause", self.pause, "#CBD5E1", TEXT)
        self.b_reset = make_btn(bf, "Reset", self.reset, "#CBD5E1", TEXT)
        for b in (self.b_start, self.b_pause, self.b_reset):
            b.pack(side="left", padx=5)

        card = ttk.Frame(r, style="Card.TFrame")
        card.pack(fill="x", padx=25, pady=8)

        f_time = ttk.Frame(card, style="Card.TFrame")
        f_time.pack(pady=10, padx=15, fill="x")
        ttk.Label(f_time, text="Kerja (menit):", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", pady=4)
        ttk.Label(f_time, text="Istirahat (menit):", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=4)
        self.e_work = ttk.Spinbox(f_time, from_=1, to=180, width=6,
                                  font=("Segoe UI", 10))
        self.e_brk = ttk.Spinbox(f_time, from_=1, to=60, width=6,
                                 font=("Segoe UI", 10))
        self.e_work.set(self.cfg["work"])
        self.e_brk.set(self.cfg["brk"])
        self.e_work.grid(row=0, column=1, padx=10, sticky="e")
        self.e_brk.grid(row=1, column=1, padx=10, sticky="e")
        ttk.Label(f_time, text="Tinggi kucing (% layar):",
                  style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        self.e_size = ttk.Spinbox(f_time, from_=20, to=95, increment=5, width=6,
                                  font=("Segoe UI", 10))
        self.e_size.set(self.cfg["size"])
        self.e_size.grid(row=2, column=1, padx=10, sticky="e")
        f_time.columnconfigure(0, weight=1)

        f_chk = ttk.Frame(card, style="Card.TFrame")
        f_chk.pack(pady=(0, 12), padx=15, fill="x")
        self.v_auto = tk.BooleanVar(value=self.cfg["auto"])
        self.v_full = tk.BooleanVar(value=self.cfg["full"])
        self.v_bg = tk.BooleanVar(value=self.cfg["bg"])
        ttk.Checkbutton(f_chk, text="Auto repeat",
                        variable=self.v_auto).pack(anchor="w", pady=3)
        ttk.Checkbutton(f_chk, text="Istirahat penuh (tanpa Shoo)",
                        variable=self.v_full).pack(anchor="w", pady=3)
        ttk.Checkbutton(f_chk, text="Saat tombol tutup ditekan, minimalkan",
                        variable=self.v_bg).pack(anchor="w", pady=3)

        f_act = tk.Frame(r, bg=BG)
        f_act.pack(fill="x", padx=25, pady=4)

        def make_act_btn(text, cmd):
            b = tk.Button(
                f_act, text=text, bg="#E2E8F0", fg=TEXT, relief="flat",
                font=("Segoe UI", 9), cursor="hand2", command=cmd, pady=4)
            b.pack(fill="x", pady=3)
            return b

        make_act_btn("Buka Desktop Pet Mochi 🐾", self.open_pet)
        make_act_btn("Pilih Kucing (PNG/GIF/WebP)", self.pick_photo)
        make_act_btn("Kembali ke Kucing Bawaan", self.clear_photo)
        make_act_btn("Panggil Kucing Sekarang (Test)", self.start_break)

        self.foto_lbl = tk.Label(r, text=self.foto_text(), bg=BG,
                                 fg=TEXT_MUTED, font=("Segoe UI", 8))
        self.foto_lbl.pack(pady=4)

        self.progress = ttk.Progressbar(r, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=25, pady=(3, 0))

        r.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()
        self.root.after(200, self.tick)

    def run(self):
        self.root.mainloop()

    def foto_text(self):
        return (os.path.basename(self.cfg["foto"]) if self.cfg.get("foto")
                else "Status: Menggunakan kucing bawaan")

    def read_cfg(self):
        try:
            work = int(self.e_work.get())
            brk = int(self.e_brk.get())
            size = int(self.e_size.get())
        except (ValueError, tk.TclError):
            messagebox.showerror(APP_NAME,
                                 "Isi durasi kerja dan istirahat dengan angka.")
            return False
        if not 1 <= work <= 180:
            messagebox.showerror(APP_NAME, "Durasi kerja harus 1–180 menit.")
            return False
        if not 1 <= brk <= 60:
            messagebox.showerror(APP_NAME, "Durasi istirahat harus 1–60 menit.")
            return False
        if not 20 <= size <= 95:
            messagebox.showerror(APP_NAME, "Tinggi kucing harus 20\u201395 persen.")
            return False
        self.cfg.update({
            "work": work, "brk": brk, "size": size,
            "auto": self.v_auto.get(), "full": self.v_full.get(),
            "bg": self.v_bg.get(),
        })
        save_cfg(self.cfg)
        return True

    def pick_photo(self):
        filetypes = [("Gambar yang didukung", "*.png *.gif *.webp *.jpg *.jpeg"),
                     ("Semua file", "*.*")]
        p = filedialog.askopenfilename(title="Pilih gambar kucing",
                                       filetypes=filetypes)
        if not p:
            return
        ext = os.path.splitext(p)[1].lower()
        if ext in (".webp", ".jpg", ".jpeg") and Image is None:
            messagebox.showerror(
                APP_NAME,
                "Format ini memerlukan Pillow.\nPasang dengan perintah:\n"
                "python -m pip install Pillow")
            return
        self.cfg["foto"] = p
        self.cache = None
        save_cfg(self.cfg)
        self.foto_lbl.config(text=self.foto_text())

    def clear_photo(self):
        self.cfg["foto"] = ""
        self.cache = None
        save_cfg(self.cfg)
        self.foto_lbl.config(text=self.foto_text())

    def _set_timer(self, mode, seconds):
        self.mode = mode
        self.remaining = max(0, int(seconds))
        self.paused = False
        self.pause_started = None
        self.deadline = time.monotonic() + self.remaining
        self.b_pause.config(text="Pause")
        self.refresh()

    def start(self):
        if not self.read_cfg():
            return
        self.close_overlay_immediate()
        self.closing = False
        self._set_timer("work", self.cfg["work"] * 60)

    def pause(self):
        if self.mode == "idle":
            return
        if not self.paused:
            self.remaining = max(0, math.ceil(self.deadline - time.monotonic()))
            self.paused = True
            self.pause_started = time.monotonic()
            self.deadline = None
            self.b_pause.config(text="Lanjut")
        else:
            self.paused = False
            self.deadline = time.monotonic() + self.remaining
            self.pause_started = None
            self.b_pause.config(text="Pause")
        self.refresh()

    def reset(self):
        self.close_overlay_immediate()
        self.mode = "idle"
        self.remaining = 0
        self.deadline = None
        self.paused = False
        self.pause_started = None
        self.b_pause.config(text="Pause")
        self.refresh()

    def _begin_break(self):
        """Mulai break memakai cfg tersimpan, tanpa dialog. Aman untuk transisi otomatis."""
        self._set_timer("break", self.cfg["brk"] * 60)
        self.open_overlay()

    def start_break(self):
        """Aksi user: validasi UI dulu, baru masuk break."""
        if not self.read_cfg():
            return
        self._begin_break()

    def end_break(self):
        self.close_overlay()
        self.beep()
        if self.cfg["auto"]:
            self._set_timer("work", self.cfg["work"] * 60)
        else:
            self.mode = "idle"
            self.remaining = 0
            self.deadline = None
            self.refresh()

    def shoo(self):
        self.close_overlay()
        self.beep()
        self._set_timer("work", self.cfg["work"] * 60)

    def tick(self):
        if self._closing_app:
            return
        if self.mode != "idle" and not self.paused and self.deadline is not None:
            self.remaining = max(0, math.ceil(self.deadline - time.monotonic()))
            if self.remaining <= 0:
                if self.mode == "work":
                    # Transisi otomatis: pakai cfg tersimpan (tidak memicu dialog).
                    self._begin_break()
                elif self.mode == "break":
                    self.end_break()
            self.refresh()
        self._lift_n += 1
        if self.ov and self.ov.winfo_exists() and self._lift_n % 10 == 0:
            try:
                if self.blocker and self.blocker.winfo_exists():
                    self.blocker.lift()
                    self.blocker.attributes("-topmost", True)
                self.ov.lift()
                self.ov.attributes("-topmost", True)
            except tk.TclError:
                pass
        self.root.after(200, self.tick)

    def refresh(self):
        names = {"idle": "Siap", "work": "Waktunya kerja",
                 "break": "Kucing sedang istirahat"}
        self.status.config(
            text=names[self.mode] + (" (dijeda)" if self.paused else ""))
        if self.mode == "idle":
            try:
                self.timer.config(text=fmt(int(self.e_work.get()) * 60))
            except (ValueError, tk.TclError):
                self.timer.config(text="--:--")
            self.progress["value"] = 0
        else:
            self.timer.config(text=fmt(self.remaining))
            total = (self.cfg["work"] if self.mode == "work"
                     else self.cfg["brk"]) * 60
            self.progress["value"] = (
                int(100 * (1 - self.remaining / total)) if total > 0 else 0)
        if self.ov_canvas and self.ov_text:
            try:
                self.ov_canvas.itemconfig(self.ov_text, text=fmt_short(self.remaining))
            except tk.TclError:
                pass

    def load_frames(self, h):
        path = self.cfg.get("foto")
        if not path or not os.path.isfile(path):
            return [], []
        key = (path, h)
        if self.cache and self.cache[0] == key:
            return self.cache[1], self.cache[2]
        frames, delays = [], []
        try:
            if Image is not None:
                with Image.open(path) as im:
                    n = max(1, getattr(im, "n_frames", 1))
                    step = max(1, math.ceil(n / 80))
                    th = max(100, min(int(h * 0.72), 1000))
                    for i in range(0, n, step):
                        im.seek(i)
                        fr = im.convert("RGBA")
                        if fr.height <= 0:
                            continue
                        fr.thumbnail((max(1, int(th * 1.6)), th),
                                     Image.Resampling.LANCZOS)
                        frames.append(ImageTk.PhotoImage(fr))
                        duration = im.info.get("duration", 80) or 80
                        delays.append(min(max(int(duration * step), 40), 500))
            else:
                ext = os.path.splitext(path)[1].lower()
                if ext not in (".png", ".gif"):
                    raise ValueError("Format ini membutuhkan Pillow.")
                if ext == ".gif":
                    for i in range(80):
                        try:
                            frames.append(tk.PhotoImage(file=path,
                                                        format=f"gif -index {i}"))
                            delays.append(80)
                        except tk.TclError:
                            break
                if not frames:
                    frames.append(tk.PhotoImage(file=path))
                    delays.append(100)
        except Exception as exc:
            messagebox.showwarning(
                APP_NAME,
                f"Gambar tidak bisa dimuat. Kucing bawaan akan digunakan.\n\n{exc}")
            return [], []
        if not frames:
            return [], []
        self.cache = (key, frames, delays)
        return frames, delays

    def animate(self):
        if not self.ov or not self.ov.winfo_exists():
            return
        started = time.monotonic()
        c = self.ov_canvas
        delay = 33
        try:
            if self.frames:
                pos = ((started - self.t0) * 1000) % self.cum[-1]
                idx = min(bisect.bisect_right(self.cum, pos), len(self.frames) - 1)
                if idx != self.fi:
                    self.fi = idx
                    c.itemconfig(self.cat_item, image=self.frames[idx])
                delay = 12
            else:
                cx, cy, scale = self.cat_geom
                if self.rig is None:
                    self.rig = MochiRig(
                        c, cx, cy, scale, "cat", bubble=True,
                        entrance_offset=self._entrance_offset,
                        walk_speed=self._entrance_speed,
                    )
                    c.tag_lower("cat")
                    c.tag_bind("cat", "<Button-1>",
                               lambda _e: (self.rig and self.rig.poke(),
                                           self.meow()))
                t = (started - self.t0) * 0.83
                half_w = max(self.ov.winfo_screenwidth() / 2, 1)
                half_h = max(self.ov.winfo_screenheight() / 2, 1)
                mx, my = self.ov.winfo_pointerxy()
                look = (max(-1, min(1, (mx - cx) / half_w)),
                        max(-1, min(1, (my - cy) / half_h)))
                # Hover dihitung dari posisi Mochi saat ini (setelah walk-in).
                rig_cx = self.rig.cx + self.rig.walk_offset
                d2 = (mx - rig_cx) ** 2 + (my - cy) ** 2
                hover = d2 < (95 * scale) ** 2
                mood = ("wave" if (self.mode == "break" and self.remaining <= 10)
                        else "idle")
                self.rig.update(t, look, mood, hover=hover)
                delay = max(1, 33 - int((time.monotonic() - started) * 1000))
        except tk.TclError:
            try:
                if not self.ov or not self.ov.winfo_exists():
                    return
            except tk.TclError:
                return
        self.anim_id = self.root.after(delay, self.animate)

    def _cancel_callback(self, attr):
        callback_id = getattr(self, attr, None)
        if callback_id:
            try:
                self.root.after_cancel(callback_id)
            except tk.TclError:
                pass
            setattr(self, attr, None)

    def fade_in(self):
        self.fade_id = None
        if not self.ov or not self.ov.winfo_exists() or self.closing:
            return
        try:
            alpha = float(self.ov.attributes("-alpha"))
            target = self._ov_alpha_target
            if alpha < target:
                self.ov.attributes("-alpha", min(alpha + 0.1, target))
                self.fade_id = self.root.after(30, self.fade_in)
        except tk.TclError:
            pass

    def open_overlay(self):
        self.close_overlay_immediate()
        self.closing = False

        bl = tk.Toplevel(self.root)
        bl.overrideredirect(True)
        bl.geometry(f"{bl.winfo_screenwidth()}x{bl.winfo_screenheight()}+0+0")
        bl.configure(bg="black", cursor="arrow")
        bl.attributes("-topmost", True)
        try:
            bl.attributes("-alpha", 0.01)
        except tk.TclError:
            try:
                bl.destroy()
            except tk.TclError:
                pass
            bl = None
        self.blocker = bl

        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        w, h = ov.winfo_screenwidth(), ov.winfo_screenheight()
        ov.geometry(f"{w}x{h}+0+0")
        ov.configure(bg=KEY)
        ov.attributes("-topmost", True)

        # Fallback ke panel gelap semi-transparan jika color-key tidak didukung.
        try:
            ov.attributes("-transparentcolor", KEY)
            canvas_bg = KEY
            self._ov_alpha_target = 1.0
        except tk.TclError:
            canvas_bg = "#111827"
            ov.configure(bg=canvas_bg)
            self._ov_alpha_target = 0.92

        try:
            ov.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

        c = tk.Canvas(ov, width=w, height=h, bg=canvas_bg,
                      highlightthickness=0)
        c.pack(fill="both", expand=True)

        self.frames, self.delays = self.load_frames(h)
        self.fi, self.t = 0, 0.0
        self.rig = None
        self.t0 = time.monotonic()
        self.cum = list(itertools.accumulate(self.delays))
        if self.frames:
            self.cat_item = c.create_image(
                w * 0.62, h - self.frames[0].height() / 2,
                image=self.frames[0], tags="cat")
            self._entrance_offset = 0.0
            self._entrance_speed = 0.0
        else:
            size = self.cfg["size"] / 100 * h / CAT_HEIGHT_UNITS
            self.cat_geom = (w * 0.6, h * 0.97 - 62 * size, size)
            # Walk-in: mulai dari luar kanan layar, sampai tengah dalam ~2 detik.
            self._entrance_offset = w * 0.75
            self._entrance_speed = self._entrance_offset / ENTRANCE_SECONDS

        rrect(c, 40, 40, 460, 270, 28, fill="#1c1c1c", outline="")
        self.ov_text = c.create_text(
            250, 155, text=fmt_short(self.remaining), fill="white",
            font=("Segoe UI Black", 96))
        if not self.cfg["full"]:
            rrect(c, w - 240, h - 120, w - 40, h - 40, 16,
                  fill=ORANGE, outline="", tags="shoo")
            c.create_text(w - 140, h - 80, text="Shoo cat", fill="white",
                          font=("Segoe UI", 20, "bold"), tags="shoo")
            c.tag_bind("shoo", "<Button-1>", lambda _event: self.shoo())
            c.tag_bind("shoo", "<Enter>",
                       lambda _event: c.config(cursor="hand2"))
            c.tag_bind("shoo", "<Leave>", lambda _event: c.config(cursor=""))

        self.ov, self.ov_canvas = ov, c
        self.animate()
        self.fade_in()

    def close_overlay(self):
        self.closing = True
        self._cancel_callback("fade_id")
        self._cancel_callback("close_id")
        if self.blocker:
            try:
                self.blocker.destroy()
            except tk.TclError:
                pass
            self.blocker = None
        if self.ov and self.ov.winfo_exists():
            try:
                alpha = float(self.ov.attributes("-alpha"))
                if alpha > 0.05:
                    self.ov.attributes("-alpha", max(alpha - 0.15, 0.0))
                    self.close_id = self.root.after(35, self.close_overlay)
                    return
            except tk.TclError:
                pass
        self.close_overlay_immediate()

    def close_overlay_immediate(self):
        self.closing = True
        self._cancel_callback("anim_id")
        self._cancel_callback("fade_id")
        self._cancel_callback("close_id")
        if self.blocker:
            try:
                self.blocker.destroy()
            except tk.TclError:
                pass
            self.blocker = None
        if self.ov:
            try:
                self.ov.destroy()
            except tk.TclError:
                pass
        self.ov = self.ov_canvas = self.ov_text = self.cat_item = None
        self.frames, self.delays = [], []
        self.rig = None
        self.cum = []
        self._entrance_offset = 0.0
        self._entrance_speed = 0.0

    def open_pet(self):
        """Open a non-blocking, draggable Mochi desktop companion."""
        if self.pet and self.pet.winfo_exists():
            self.pet.deiconify()
            self.pet.lift()
            self.pet.attributes("-topmost", True)
            return
        pet = tk.Toplevel(self.root)
        pet.overrideredirect(True)
        pet.configure(bg=KEY)
        pet.attributes("-topmost", True)
        try:
            pet.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pet.configure(bg="#F8FAFC")
        width, height = 190, 160
        sw, sh = pet.winfo_screenwidth(), pet.winfo_screenheight()
        self.pet_x = max(0, sw - width - 30)
        self.pet_y = max(0, sh - height - 70)
        pet.geometry(f"{width}x{height}+{self.pet_x}+{self.pet_y}")
        canvas = tk.Canvas(pet, width=width, height=height, bg=pet.cget("bg"),
                           highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        self.pet, self.pet_canvas = pet, canvas
        self.pet_t0 = time.monotonic()
        self.pet_last = 0.0
        self.pet_n = 0
        self.pet_rig = MochiRig(canvas, 91, 59, 1.0, "mochi")
        self.pet_sleeping = False
        self.pet_walk = True
        self.pet_dx = PET_SPEED_PX

        canvas.bind("<Button-1>", self.pet_click)
        canvas.bind("<ButtonPress-3>", self.pet_menu)
        canvas.bind("<ButtonPress-1>", self.pet_drag_start, add="+")
        canvas.bind("<B1-Motion>", self.pet_drag_move)
        canvas.bind("<ButtonRelease-1>", self.pet_drag_end)
        pet.bind("<ButtonPress-3>", self.pet_menu)
        self.pet_tick()

    def pet_tick(self):
        if not self.pet or not self.pet_canvas or not self.pet_rig:
            return
        try:
            if not self.pet.winfo_exists():
                return
        except tk.TclError:
            return
        started = time.monotonic()
        dt = min(started - self.pet_last, 0.1) if self.pet_last else 0.033
        self.pet_last = started
        self.pet_n += 1
        t = (started - self.pet_t0) * 1.33
        mood = "sleep" if self.pet_sleeping else (
            "happy" if started < self.pet_happy_until else
            "walk" if self.pet_walk else "idle")
        try:
            if mood == "walk" and not self.pet_drag:
                sw = self.pet.winfo_screenwidth()
                old = int(self.pet_x)
                self.pet_x += self.pet_dx * dt
                if self.pet_x <= 0 or self.pet_x >= sw - 190:
                    self.pet_dx = -self.pet_dx
                    self.pet_x = max(0, min(sw - 190, self.pet_x))
                if int(self.pet_x) != old:
                    self.pet.geometry(
                        f"190x160+{int(self.pet_x)}+{int(self.pet_y)}")
            px, py = self.pet.winfo_pointerxy()
            look = (max(-1, min(1, (px - (self.pet_x + 95)) / 300)),
                    max(-1, min(1, (py - (self.pet_y + 65)) / 250)))
            hover = (self.pet_x <= px <= self.pet_x + 190 and
                     self.pet_y <= py <= self.pet_y + 160)
            self.pet_rig.update(t, look, mood, hover=hover)
            if self.pet_n % 60 == 0:
                self.pet.attributes("-topmost", True)
        except tk.TclError:
            try:
                if not self.pet or not self.pet.winfo_exists():
                    return
            except tk.TclError:
                return
        elapsed = int((time.monotonic() - started) * 1000)
        self.pet_anim_id = self.root.after(max(1, 33 - elapsed), self.pet_tick)

    def pet_click(self, _event=None):
        self.pet_sleeping = False
        self.pet_happy_until = time.monotonic() + 1.8
        if self.pet_rig:
            self.pet_rig.poke()
        self.meow()

    def pet_drag_start(self, event):
        if event.num == 1:
            self.pet_drag = (event.x_root - self.pet_x,
                             event.y_root - self.pet_y)
            self._pet_was_walking = self.pet_walk
            self.pet_walk = False

    def pet_drag_move(self, event):
        if not self.pet_drag or not self.pet:
            return
        self.pet_x = max(0, event.x_root - self.pet_drag[0])
        self.pet_y = max(0, event.y_root - self.pet_drag[1])
        try:
            self.pet.geometry(f"190x160+{int(self.pet_x)}+{int(self.pet_y)}")
        except tk.TclError:
            pass

    def pet_drag_end(self, _event=None):
        self.pet_drag = None
        if self._pet_was_walking:
            self.pet_walk = True
        self._pet_was_walking = False

    def pet_menu(self, event=None):
        if not self.pet or not self.pet.winfo_exists():
            return
        menu = tk.Menu(self.pet, tearoff=0)
        menu.add_command(label="Panggil Mochi", command=self.pet_click)
        menu.add_command(label="Mulai / hentikan jalan",
                         command=self.toggle_pet_walk)
        menu.add_command(label="Mode tidur", command=self.toggle_pet_sleep)
        menu.add_separator()
        menu.add_command(label="Tutup Mochi", command=self.close_pet)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def toggle_pet_walk(self):
        self.pet_walk = not self.pet_walk
        if self.pet_walk:
            self.pet_sleeping = False

    def toggle_pet_sleep(self):
        self.pet_sleeping = not self.pet_sleeping
        if self.pet_sleeping:
            self.pet_walk = False
        else:
            self.pet_happy_until = time.monotonic() + 0.8

    def close_pet(self):
        if self.pet_anim_id:
            try:
                self.root.after_cancel(self.pet_anim_id)
            except tk.TclError:
                pass
            self.pet_anim_id = None
        if self.pet:
            try:
                self.pet.destroy()
            except tk.TclError:
                pass
        self.pet = self.pet_canvas = self.pet_rig = None
        self.pet_drag = None

    def meow(self):
        """Mainkan meow/dengkur; fallback ke beep kalau gagal."""
        if winsound is None:
            self.beep()
            return
        try:
            os.makedirs(SND_DIR, exist_ok=True)
            kind = "purr" if random.random() < 0.25 else "meow"
            path = os.path.join(SND_DIR, kind + ".wav")
            if not os.path.isfile(path):
                if kind == "meow":
                    _synth(path, 0.7, _meow_fn())
                else:
                    _synth(path, 1.6, _purr_fn())
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except (OSError, RuntimeError):
            pass

    def beep(self):
        """Bunyi singkat yang tidak memblokir event loop Tk."""
        if winsound is not None:
            threading.Thread(target=_beep_async, daemon=True).start()
            return
        try:
            self.root.bell()
        except tk.TclError:
            pass

    def on_close(self):
        if self.v_bg.get():
            self.root.iconify()
            return
        self._closing_app = True
        self.close_pet()
        self.close_overlay_immediate()
        try:
            self.root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    App().run()