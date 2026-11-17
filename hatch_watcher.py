"""
Secret Hatch Notifer+  (black / red)
------------------------------
Watches your Roblox windows for the secret-hatch screen (the big BLACK egg
silhouette in the middle of the screen, or the "Hold to Reveal!" text).
When it sees one it posts to the shared community Discord channel, pings the
Discord ID you typed in, and attaches a picture of that Roblox window.

What it sends: your Discord ID mention, the account name, and an image of the
Roblox window only (never your whole desktop). It sends nothing until a secret
hatch is detected or you press a Test button. Windows only.
"""
import sys

if sys.platform != "win32":
    sys.exit("Secret Hatch Notifer+ only runs on Windows.")

import webbrowser, tempfile, subprocess, winsound
import io, os, re, json, time, queue, ctypes, base64, datetime, threading

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import cv2
cv2.setNumThreads(1)
from alert_guard import AlertGuard, reveal_reason
import numpy as np
import psutil
import requests
import mss
import win32con, win32gui, win32process, win32ui
from PIL import Image
from tkinter import ttk, messagebox, filedialog
import tkinter as tk
import customtkinter as ctk

import math
from PIL import ImageDraw

# The look: deep ink blue. Gold means one thing only, a secret (and the button that starts the hunt).
BG, SIDEBAR, PANEL, PANEL2, BORDER, HOVER = "#12131a", "#0d0e13", "#1a1c26", "#242736", "#30344a", "#2f3349"
TEXT, MUTED, FAINT, STAGE = "#eef0f6", "#959bb0", "#62687e", "#07080c"
GOLD, GOLD_HOT, GOLD_DIM, ON_GOLD = "#ffb224", "#ffc95c", "#3a2f16", "#1b1300"
GREEN, GREEN_DIM, DANGER, DANGER_DIM, DANGER_TEXT = "#3ddc97", "#16332a", "#f0556a", "#3a1a22", "#ff9aa8"
AMBER = GOLD
CHIP_BG = {GREEN: GREEN_DIM, GOLD: GOLD_DIM, DANGER: DANGER_DIM}
# Fonts that ship with Windows, so nothing extra has to be bundled with the exe.
FONT, FONT_HEAD, FONT_MONO = "Segoe UI", "Bahnschrift", "Consolas"


def F(size=13, bold=False, head=False, mono=False):
    return ctk.CTkFont(family=FONT_MONO if mono else (FONT_HEAD if head else FONT), size=size,
                       weight="bold" if bold else "normal")


def egg_image(fill, ring, px=64, glow=None):
    """The egg silhouette used as the app's status light. Drawn large and shrunk so the edge stays smooth.

    With glow set, the egg sits a little smaller inside a soft halo of that colour."""
    S = px * 8
    k = 0.80 if glow else 1.0
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    if glow:
        yy, xx = np.mgrid[0:S, 0:S]
        r = np.hypot(xx - S / 2, yy - S / 2) / (S / 2)
        a = (np.clip(1 - r, 0, 1) ** 1.3 * 150).astype(np.uint8)
        rgb = [int(glow[i:i + 2], 16) for i in (1, 3, 5)]
        halo = np.dstack([np.full((S, S), c, np.uint8) for c in rgb] + [a])
        im = Image.fromarray(halo, "RGBA")
    d = ImageDraw.Draw(im)

    def outline(grow):
        pts = []
        for i in range(160):
            a = 2 * math.pi * i / 160
            y = -math.cos(a)  # -1 is the top of the egg, 1 the bottom
            x = math.sin(a) * (0.80 + 0.13 * y)  # narrower towards the top
            pts.append((S / 2 + x * S * (0.40 + grow) * k, S * 0.51 + y * S * (0.43 + grow) * k))
        return pts

    d.polygon(outline(0.045), fill=ring)
    d.polygon(outline(0.0), fill=fill)
    return im.resize((px * 2, px * 2), Image.LANCZOS)

APP_VERSION = "2.5.2"
# Where the app looks for new versions. Use ONE of:
#   - a GitHub releases link:  "https://api.github.com/repos/YOUR-NAME/YOUR-REPO/releases/latest"
#   - a link to a small JSON file you host:  {"version": "1.1.0", "url": "https://download-link", "notes": "what's new"}
# Leave it empty to turn update checking off.
UPDATE_URL = "https://api.github.com/repos/jonathanbro12838-hub/hatcher-releases/releases/latest"

GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE, LWA_ALPHA = -20, 0x80000, 0x20, 0x08000000, 2
ROBLOX_EXES = ("robloxplayerbeta.exe", "robloxplayer.exe", "windows10universal.exe")
ID_RE = re.compile(r"^\d{17,20}$")
FULL_W, FULL_H = 1280, 720

def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


SOUND_PATH = resource_path("alert.wav")


def play_sound():
    try:
        winsound.PlaySound(SOUND_PATH, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass


# Settings and stats live in AppData, so the exe works from anywhere (Downloads, Desktop, a USB stick)
# and never leaves extra files next to it.
APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Secret Hatch Notifer+")
try:
    os.makedirs(APP_DIR, exist_ok=True)
except Exception:
    APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
STATS_PATH = os.path.join(APP_DIR, "stats.json")
DEFAULTS = {"discord_id": "", "rearm": 6, "interval": 1.0, "peek": 5, "minimized": True,
            "fullsize": False, "reveal_delay": 8, "dc_warn": True, "reveal_pic": False,
            "sound_ui": True, "sound_alert": True}

# Shared community webhook, kept out of the UI and not stored in plain text.
# (Anyone determined can still extract it from a client; see README notes.)
try:
    import hw_secrets as _S  # written at build time from GitHub secrets / webhooks.txt, never stored in the repo
except Exception:
    _S = None
_K = b"hw-community-v1"


def _hook(kind="main"):
    blob = getattr(_S, "TEST" if kind == "test" else "MAIN", "") if _S else ""
    if not blob:
        raise RuntimeError("this copy was built without a Discord webhook")
    raw = base64.b64decode(blob)
    return bytes(c ^ _K[i % len(_K)] for i, c in enumerate(raw)).decode()


def is_reveal_text(text):
    t = re.sub(r"[^a-z0-9]", "", text.lower()).replace("0", "o").replace("1", "l")
    if "holdtoreve" in t or "holdtoreal" in t or "toreveal" in t:
        return True
    return "reveal" in t and "hold" in t


def find_black_egg(frame):
    """True if a big solid-black silhouette (the unrevealed secret) sits in the middle of the window.

    Works for tiny, narrow, tall or half-snapped windows: it only looks at the shape's RELATIVE size and position,
    and it is fine if the shape runs into the window edges (a narrow window crops the sides of the scene)."""
    if frame is None or frame.size == 0:
        return False
    h, w = frame.shape[:2]
    if h < 40 or w < 40:
        return False
    sc = 480.0 / max(h, w)
    sw, sh = max(24, int(w * sc)), max(24, int(h * sc))
    small = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_LINEAR)
    H, W = small.shape[:2]
    peak = small.max(axis=2)
    dark = (peak < 16).astype(np.uint8)
    if dark.mean() > 0.75:  # almost all black = loading screen / black capture, not a secret
        return False
    if (peak > 60).mean() < 0.10:  # needs real picture around it (the game UI), not just a few pixels
        return False
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    n, _, stats, cents = cv2.connectedComponentsWithStats(dark, connectivity=8)
    lum = small.mean(axis=2)
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if not (0.04 <= area / float(W * H) <= 0.80):
            continue
        cx, cy = cents[i]
        if not (0.20 * W <= cx <= 0.80 * W and 0.15 * H <= cy <= 0.85 * H):
            continue
        if area / float(bw * bh) < 0.50 or not (0.35 <= bh / float(bw) <= 3.5):
            continue
        pad = 6
        x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(W, x + bw + pad), min(H, y + bh + pad)
        ring = lum[y0:y1, x0:x1].copy()
        ring[(y - y0):(y - y0 + bh), (x - x0):(x - x0 + bw)] = np.nan
        if np.isfinite(ring).any() and np.nanmean(ring) < 20:
            continue
        return True
    return False


def is_disconnect_text(text):
    t = re.sub(r"[^a-z0-9]", "", text.lower())
    keys = ("disconnected", "errorcode", "youwerekicked", "lostconnection", "connectionerror",
            "serverhasshutdown", "servershutdown", "connectionattemptfailed")
    return any(k in t for k in keys)


def parse_version(v):
    nums = re.findall(r"\d+", str(v))
    return tuple(int(n) for n in nums[:4]) or (0,)


def fetch_update_info():
    r = requests.get(UPDATE_URL, timeout=8, headers={"Accept": "application/json", "User-Agent": "SecretHatchNotifer"})
    r.raise_for_status()
    d = r.json()
    ver = d.get("tag_name") or d.get("version") or ""
    url = d.get("html_url") or d.get("url") or ""
    notes = d.get("body") or d.get("notes") or ""
    asset = ""
    for a in d.get("assets") or []:
        if str(a.get("name", "")).lower().endswith(".exe"):
            asset = a.get("browser_download_url") or ""
            break
    if not asset:
        asset = d.get("download") or ""
    return str(ver), str(url), str(notes), str(asset)


def load_stats():
    st = {"total": 0, "accounts": {}, "history": []}
    try:
        with open(STATS_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        st["total"] = int(d.get("total", 0))
        st["accounts"] = {str(k): int(v) for k, v in d.get("accounts", {}).items()}
        st["history"] = [h for h in d.get("history", []) if "t" in h and "account" in h]
    except Exception:
        pass
    return st


def save_stats(stats):
    try:
        with open(STATS_PATH, "w", encoding="utf-8") as f:
            json.dump(stats, f)
    except Exception:
        pass


def make_thumb(frame, w=448, h=252):
    img = Image.fromarray(frame)
    img.thumbnail((w, h), Image.BILINEAR)
    canvas = Image.new("RGB", (w, h), (0, 0, 0))
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return canvas


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            for k, v in json.load(f).items():
                if k in cfg:
                    cfg[k] = v
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def fmt_uptime(sec):
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def to_jpeg_bytes(frame, max_width=1280):
    img = Image.fromarray(frame)
    if img.width < 800:  # tiny window: enlarge so the picture in Discord is still easy to see
        img = img.resize((800, max(1, int(img.height * 800 / img.width))), Image.LANCZOS)
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def post_discord(payload, image_bytes=None, ping_id=None, test=False):
    payload = dict(payload)
    payload["allowed_mentions"] = {"users": [ping_id]} if ping_id else {"parse": []}
    if image_bytes:
        r = requests.post(_hook("test" if test else "main"), data={"payload_json": json.dumps(payload)},
                          files={"files[0]": ("secret.jpg", image_bytes, "image/jpeg")}, timeout=20)
    else:
        r = requests.post(_hook("test" if test else "main"), json=payload, timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"Discord replied {r.status_code}: {r.text[:120]}")


# --------------------------------------------------------------------------
# Windows: find Roblox windows and capture them
# --------------------------------------------------------------------------
def find_roblox_windows(name_cache):
    found = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title.strip().lower().startswith("roblox"):
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return True
        if pid not in name_cache:
            try:
                name_cache[pid] = psutil.Process(pid).name().lower()
            except Exception:
                name_cache[pid] = ""
        if name_cache[pid] in ROBLOX_EXES:
            found.append((hwnd, pid, title))
        return True

    win32gui.EnumWindows(callback, None)
    return found


def client_geometry(hwnd):
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    sx, sy = win32gui.ClientToScreen(hwnd, (0, 0))
    return sx, sy, right - left, bottom - top


def is_small(hwnd):
    """True if the window is smaller than a normal game view (and not maximized)."""
    try:
        if win32gui.IsZoomed(hwnd):
            return False
        _, _, cw, ch = client_geometry(hwnd)
        return cw < FULL_W - 40 or ch < FULL_H - 40
    except Exception:
        return False


def bring_to_front(hwnd):
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        try:
            ctypes.windll.user32.FlashWindow(hwnd, True)
        except Exception:
            pass


def looks_incomplete(frame):
    """True if the frame is missing or two or more quarters of it are pure black (a partial window capture)."""
    if frame is None or frame.size == 0:
        return True
    h, w = frame.shape[:2]
    if h < 20 or w < 20:
        return True
    black = 0
    for ys in (slice(0, h // 2), slice(h // 2, h)):
        for xs in (slice(0, w // 2), slice(w // 2, w)):
            if frame[ys, xs][::8, ::8].max() < 8:
                black += 1
    return black >= 2


class Grabber:
    """Captures the whole game area of a window. One instance per thread."""

    def __init__(self):
        self._sct = None

    def _screen(self, hwnd):
        sx, sy, cw, ch = client_geometry(hwnd)
        if cw < 10 or ch < 10:
            return None
        if self._sct is None:
            self._sct = mss.mss()
        shot = self._sct.grab({"left": sx, "top": sy, "width": cw, "height": ch})
        return np.ascontiguousarray(np.asarray(shot)[:, :, :3][:, :, ::-1])

    def _printwindow(self, hwnd):
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        if w < 10 or h < 10:
            return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bitmap)
            ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)  # 2 = render full content
            bits = bitmap.GetBitmapBits(True)
            arr = np.frombuffer(bits, dtype=np.uint8).reshape(h, w, 4)
            sx, sy, cw, ch = client_geometry(hwnd)
            ox, oy = sx - left, sy - top
            arr = arr[oy:oy + ch, ox:ox + cw, :3][:, :, ::-1]
            return np.ascontiguousarray(arr)
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)

    def grab(self, hwnd, mode):
        if mode.startswith("Screen"):
            return self._screen(hwnd)
        frame = self._printwindow(hwnd)
        if mode.startswith("Window"):
            return frame
        # Auto: fall back to a screen grab if the window capture came back black or only partly drawn
        if frame is None or frame.size == 0 or frame.mean() < 2 or looks_incomplete(frame):
            try:
                alt = self._screen(hwnd)
            except Exception:
                alt = None
            if alt is not None and not looks_incomplete(alt):
                return alt
            if frame is None or frame.size == 0 or frame.mean() < 2:
                return alt
        return frame

    def peek(self, hwnd, mode, settle=1.2, full=False):
        """Look at a MINIMIZED window without the user seeing anything.

        The window is restored without taking focus, made fully see-through and click-through, read through its own
        render (PrintWindow), then minimized again and its normal style put back. If that gives a black picture it
        falls back to a very short visible peek."""
        ex_old = None
        frame = None
        try:
            ex_old = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)
            win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, ex_old | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            win32gui.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA)  # fully see-through
        except Exception:
            ex_old = None
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 3, ctypes.byref(ctypes.c_int(1)), 4)  # no minimize/restore animation
        except Exception:
            pass
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
            time.sleep(settle)  # give the game time to draw a fresh frame
            for attempt in range(2):
                frame = self._printwindow(hwnd)
                if frame is not None and frame.size and frame.mean() >= 2:
                    return frame
                time.sleep(0.8)
            if ex_old is not None:  # the invisible read came back black: show it for a moment instead
                try:
                    win32gui.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
                except Exception:
                    pass
                time.sleep(0.5)
                alt = self.grab(hwnd, mode)
                if alt is not None and alt.size:
                    return alt
            return frame
        finally:
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWMINNOACTIVE)
            except Exception:
                pass
            if ex_old is not None:
                try:
                    win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, ex_old)
                except Exception:
                    pass
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 3, ctypes.byref(ctypes.c_int(0)), 4)
            except Exception:
                pass

    def capture(self, hwnd, mode, allow_peek=True, full=False):
        if win32gui.IsIconic(hwnd):
            return self.peek(hwnd, mode, full=full) if allow_peek else None
        return self.grab(hwnd, mode)  # never resizes or moves the user's window



def snap_window(hwnd, allow_peek=True, full=True):
    """Picture of ONE Roblox window (its game area only), never the desktop or other windows.

    Uses the window's own render (PrintWindow), so windows on top of it don't show up. A screen grab is only
    used if that render is black AND nothing else is covering the middle of the window."""
    g = Grabber()
    frame = g.capture(hwnd, "Window", allow_peek=allow_peek, full=full)
    if frame is not None and frame.size and frame.mean() >= 2 and not looks_incomplete(frame):
        return frame
    try:
        sx, sy, cw, ch = client_geometry(hwnd)
        top = ctypes.windll.user32.GetAncestor(win32gui.WindowFromPoint((sx + cw // 2, sy + ch // 2)), 2)
        if top == hwnd:  # window is on top, so a screen grab of its area is just this window
            alt = g.capture(hwnd, "Screen", allow_peek=allow_peek, full=full)
            if alt is not None and alt.size:
                return alt
    except Exception:
        pass
    return frame


def icon_image(name, color, px=18):
    """Small line icons, drawn here so the exe needs no icon files. Works on a 24-unit grid like most icon sets."""
    S = px * 8
    u = S / 24.0
    w = max(1, int(round(1.9 * u)))
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def box(x0, y0, x1, y1, r=2.0, solid=False):
        d.rounded_rectangle((x0 * u, y0 * u, x1 * u, y1 * u), radius=r * u, fill=color if solid else None,
                            outline=None if solid else color, width=0 if solid else w)

    def dot(x, y, r):
        d.ellipse(((x - r) * u, (y - r) * u, (x + r) * u, (y + r) * u), fill=color)

    def line(*pts):
        d.line([(x * u, y * u) for x, y in pts], fill=color, width=w, joint="curve")
        for x, y in (pts[0], pts[-1]):
            dot(x, y, w / u / 2)

    if name == "grid":
        for x, y in ((3, 3), (13.5, 3), (3, 13.5), (13.5, 13.5)):
            box(x, y, x + 7.5, y + 7.5, 2.2)
    elif name == "bars":
        box(4, 12, 8, 20, 1.4, True)
        box(10, 4, 14, 20, 1.4, True)
        box(16, 9, 20, 20, 1.4, True)
    elif name == "list":
        for y in (6, 12, 18):
            dot(4.2, y, 1.3)
            line((8.5, y), (20, y))
    elif name == "sliders":
        line((4, 8), (20, 8))
        line((4, 16), (20, 16))
        dot(9, 8, 3.1)
        dot(15, 16, 3.1)
    elif name == "egg":
        pts = []
        for i in range(120):
            a = 2 * math.pi * i / 120
            y = -math.cos(a)
            pts.append(((12 + math.sin(a) * (0.80 + 0.13 * y) * 8.6) * u, (12.3 + y * 9.4) * u))
        d.polygon(pts, fill=color)
    elif name == "eye":
        d.ellipse((2 * u, 6 * u, 22 * u, 18 * u), outline=color, width=w)
        dot(12, 12, 3.0)
    elif name == "clock":
        d.ellipse((3 * u, 3 * u, 21 * u, 21 * u), outline=color, width=w)
        line((12, 7.5), (12, 12), (15.5, 14.2))
    elif name == "pc":
        box(3, 4, 21, 16, 2.2)
        line((8, 20.2), (16, 20.2))
    elif name == "pulse":
        line((2.5, 13), (7.5, 13), (10.5, 5.5), (14, 19), (16.8, 11), (21.5, 11))
    elif name == "calendar":
        box(3.5, 5, 20.5, 20.5, 2.4)
        line((3.5, 10), (20.5, 10))
        line((8, 2.8), (8, 6))
        line((16, 2.8), (16, 6))
    return im.resize((px * 2, px * 2), Image.LANCZOS)


def mix(a, b, t):
    """The colour t of the way from a to b (both '#rrggbb')."""
    return "#" + "".join(f"{round(int(a[i:i + 2], 16) + (int(b[i:i + 2], 16) - int(a[i:i + 2], 16)) * t):02x}"
                         for i in (1, 3, 5))


def pointer_inside(w):
    try:
        x, y = w.winfo_pointerxy()
        x0, y0 = w.winfo_rootx(), w.winfo_rooty()
        return x0 <= x < x0 + w.winfo_width() and y0 <= y < y0 + w.winfo_height()
    except Exception:
        return False


def ago(t):
    s = max(0, time.time() - t)
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    return f"{int(s // 86400)}d ago"


def make_hover_sound():
    """Writes the tiny tick played when the pointer lands on a button (once, into the app's data folder)."""
    import wave, struct
    path = os.path.join(APP_DIR, "hover.wav")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 200:
            return path
        rate, n = 22050, int(22050 * 0.030)
        frames, phase = [], 0.0
        for i in range(n):
            t = i / n
            phase += 2 * math.pi * (2300 - 900 * t) / rate  # a quick falling chirp reads as a soft "tk"
            frames.append(struct.pack("<h", int(32767 * 0.10 * math.exp(-5.5 * t) * min(1, i / 20) * math.sin(phase))))
        with wave.open(path, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(rate)
            f.writeframes(b"".join(frames))
        return path
    except Exception:
        return ""


HOVER_PATH = ""


def play_hover():
    """Never cuts off a sound that is already playing, so it can't interrupt the secret alert."""
    try:
        if HOVER_PATH:
            winsound.PlaySound(HOVER_PATH, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NOSTOP
                               | winsound.SND_NODEFAULT)
    except Exception:
        pass


class Hover:
    """Fades a button between its normal and hover colour, and ticks softly when the pointer arrives."""

    def __init__(self, app, widget, base, hot, paint, sound=True):
        self.app, self.w, self.base, self.hot, self.paint, self.sound = app, widget, base, hot, paint, sound
        self.t, self.target, self.inside, self.busy = 0.0, 0, False, False
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")

    def set(self, base, hot):
        """New colours, for a button that changes role (the active page, Start turning into Stop)."""
        self.base, self.hot = base, hot
        self.paint(mix(base, hot, self.t))

    def _enter(self, _e=None):
        if not self.inside:
            self.inside = True
            if self.sound:
                self.app._hover_tick()
        self._go(1)

    def _leave(self, _e=None):
        try:
            self.w.after(20, self._check)  # moving between a button's own parts also fires Leave
        except Exception:
            pass

    def _check(self):
        if not pointer_inside(self.w):
            self.inside = False
            self._go(0)

    def _go(self, target):
        self.target = target
        if not self.busy:
            self.busy = True
            self._step()

    def _step(self):
        try:
            if not self.w.winfo_exists():
                return
            if self.target > self.t:
                self.t = min(1.0, self.t + 0.34)
            else:
                self.t = max(0.0, self.t - 0.22)
            self.paint(mix(self.base, self.hot, self.t))
            if self.t != self.target:
                self.w.after(16, self._step)
            else:
                self.busy = False
        except Exception:
            self.busy = False


class Tile(ctk.CTkFrame):
    """One number on the Accounts page: icon, label, value, and a small chip saying how it compares."""

    def __init__(self, master, app, icon, title):
        super().__init__(master, fg_color=PANEL, border_width=1, border_color=BORDER, corner_radius=12)
        self.shown = None
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 0))
        ctk.CTkLabel(top, text="", image=app._icon(icon, MUTED, 14), width=24, height=24, fg_color=PANEL2,
                     corner_radius=7).pack(side="left")
        ctk.CTkLabel(top, text=title, text_color=MUTED, font=F(12), height=24).pack(side="left", padx=(8, 0))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=13, pady=(2, 10))
        self.val = ctk.CTkLabel(row, text="0", text_color=TEXT, font=F(23, True, head=True), height=28, anchor="w")
        self.val.pack(side="left")
        self.note = ctk.CTkLabel(row, text="", text_color=MUTED, fg_color=PANEL2, corner_radius=8, height=20,
                                 font=F(11, True))

    def set(self, v, note="", color=MUTED, hot=False):
        sig = (v, note, color, hot)
        if sig == self.shown:
            return
        self.shown = sig
        self.val.configure(text=f"{v:,}" if isinstance(v, int) else str(v), text_color=GOLD if hot else TEXT)
        if note:
            self.note.configure(text=f" {note} ", text_color=color, fg_color=CHIP_BG.get(color, PANEL2))
            self.note.pack(side="left", padx=(8, 0), pady=(4, 0))
        else:
            self.note.pack_forget()


class AccountCard(ctk.CTkFrame):
    """One Roblox window: live picture, name, memory, status, watch switch and a menu."""

    def __init__(self, master, app, st):
        super().__init__(master, fg_color=PANEL, border_width=2, border_color=BORDER, corner_radius=12)
        self.app, self.hwnd, self.seen_v, self.last = app, st.hwnd, -1, None
        self.tw = self.th = 0
        self.ci = None
        self.hot = self.sel = self.hovered = False
        self.img = ctk.CTkLabel(self, text="Previews disabled · Low CPU", text_color=FAINT, fg_color=STAGE,
                                corner_radius=0, font=F(12))
        self.img.pack(padx=10, pady=(10, 0))
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(6, 0))
        self.name = ctk.CTkLabel(top, text=st.label, text_color=TEXT, anchor="w", font=F(13, True), height=22)
        self.name.pack(side="left")
        self.menu_btn = ctk.CTkButton(top, text="•••", width=32, height=22, corner_radius=7, fg_color=PANEL,
                                      hover=False, text_color=MUTED, font=F(11), command=self._menu)
        self.menu_btn.pack(side="right")
        Hover(app, self.menu_btn, PANEL, HOVER, lambda c: self.menu_btn.configure(fg_color=c))
        self.mem = ctk.CTkLabel(top, text="", text_color=FAINT, font=F(11), height=22)
        self.mem.pack(side="right", padx=(0, 4))
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=10, pady=(4, 10))
        self.status = ctk.CTkLabel(bot, text="", text_color=MUTED, fg_color=PANEL2, corner_radius=10, height=22,
                                   font=F(11, True))
        self.status.pack(side="left")
        self.watch_var = tk.BooleanVar(value=st.watch)
        ctk.CTkSwitch(bot, text="", variable=self.watch_var, command=self._toggle, width=36, switch_width=36,
                      switch_height=19, progress_color=GREEN, fg_color=BORDER, button_color=TEXT,
                      button_hover_color="#ffffff").pack(side="right")
        for w in (self, top, bot, self.img, self.name, self.mem, self.status):
            w.bind("<Button-1>", self._select)
            w.bind("<Double-1>", lambda e: self.app.open_view(self.hwnd))
            w.bind("<Enter>", self._enter, add="+")
            w.bind("<Leave>", self._leave, add="+")
        self.set_size(236)

    def set_size(self, width):
        """Fits the card to the column width the grid worked out, keeping the picture 16:9."""
        tw = max(150, int(width) - 24)  # 24 = the padding and border around the picture
        if tw == self.tw:
            return
        self.tw, self.th = tw, int(round(tw * 9 / 16))
        self.img.configure(width=self.tw, height=self.th)
        if self.ci is not None:
            self.ci.configure(size=(self.tw, self.th))

    def _enter(self, _e=None):
        if not self.hovered:
            self.hovered = True
            self.app._hover_tick()
            self._border()

    def _leave(self, _e=None):
        self.after(20, self._check)

    def _check(self):
        if self.winfo_exists() and not pointer_inside(self):
            self.hovered = False
            self._border()

    def _border(self):
        self.configure(border_color=GOLD if self.hot else ("#8990ad" if self.sel else
                                                           ("#4b5173" if self.hovered else BORDER)))

    def _select(self, _e=None):
        self.app.select(self.hwnd)

    def _toggle(self):
        self.app._click()
        self.app.set_watch(self.hwnd, bool(self.watch_var.get()))

    def _menu(self):
        self.app._click()
        self.app.select(self.hwnd)
        m = tk.Menu(self, tearoff=0, bg=PANEL2, fg=TEXT, activebackground=HOVER, activeforeground=TEXT, bd=0,
                    relief="flat", font=(FONT, 10))
        m.add_command(label="Open live view", command=self.app.on_view)
        m.add_command(label="Check what the app sees (sends nothing)", command=self.app.on_test_read)
        m.add_command(label="Send a test alert from this account", command=self.app.on_test_alert)
        m.add_separator()
        m.add_command(label="Rename…", command=self.app.on_rename)
        m.add_command(label="Bring Roblox to the front", command=self.app.on_front)
        try:
            m.tk_popup(self.menu_btn.winfo_rootx(), self.menu_btn.winfo_rooty() + self.menu_btn.winfo_height())
        finally:
            m.grab_release()

    def refresh(self, st, mem, status, color, selected):
        sig = (st.label, mem, status, color, selected, st.watch, st.hit)
        if sig != self.last:
            self.last = sig
            self.name.configure(text=st.label[:18])
            self.mem.configure(text=mem)
            self.status.configure(text=f"  ●  {status}  ", text_color=color, fg_color=CHIP_BG.get(color, PANEL2))
            self.hot, self.sel = bool(st.hit and st.watch), selected
            self._border()
            if self.watch_var.get() != st.watch:
                self.watch_var.set(st.watch)
        if st.thumb is not None and st.thumb_v != self.seen_v:
            self.seen_v = st.thumb_v
            self.ci = ctk.CTkImage(light_image=st.thumb, dark_image=st.thumb, size=(self.tw, self.th))
            self.img.configure(image=self.ci, text="")


class WinState:
    def __init__(self, hwnd, pid, label):
        self.hwnd, self.pid, self.label = hwnd, pid, label
        self.watch, self.armed = True, True
        self.alert_guard = AlertGuard()
        self.streak = 0
        self.last_peek = 0.0
        self.gone_since = None
        self.dc_streak = self.dc_clear = 0
        self.thumb, self.thumb_v, self.thumb_t = None, 0, 0.0
        self.last_txt = self.last_full = 0.0
        self.dc_sent = False
        self.hit = False
        self.status = "Starting…"
        try:
            self.proc = psutil.Process(pid)
            self.proc.cpu_percent(None)
        except Exception:
            self.proc = None


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("Secret Hatch Notifer+")
        self.geometry("1240x780")
        self.minsize(1080, 700)
        self.configure(fg_color=BG)
        try:  # CustomTkinter sets its own icon after startup, so set ours slightly later
            self.after(250, lambda: self.iconbitmap(resource_path("icon.ico")))
        except Exception:
            pass
        self.cfg = load_config()
        self.settings = dict(self.cfg)
        self.states, self.name_cache = {}, {}
        self.counter = 0
        self.log_q = queue.Queue()
        self.stop = threading.Event()
        self.running = False
        self.ocr = None
        self.ocr_lock = threading.Lock()
        self.last_test = 0.0
        self.stats = load_stats()
        self.stats_lock = threading.Lock()
        self.session = {}
        self._tick_n = 0
        self._last_saved = None
        self.page, self.selected = "workspace", None
        self.cards, self.card_order, self.pages, self.nav, self.kv = {}, [], {}, {}, {}
        self.dc_count = self.fail_count = 0
        self.update_info, self._updating = None, False
        self._build_ui()
        self._sync()
        self.scan_windows()
        self.after(2500, self._tick)
        self.after(4000, self._periodic_update_check)
        threading.Thread(target=self._worker, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log("Ready. Paste your Discord user ID, then press Start watching.")

    # ------------------------------------------------------------------ UI
    def _click(self):
        if self.settings.get("sound_ui", True):
            play_sound()

    def _snd(self, cmd):
        def inner():
            self._click()
            cmd()
        return inner

    def _card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=PANEL, border_width=1, border_color=BORDER, corner_radius=14, **kw)

    def _btn(self, parent, text, cmd, kind="quiet", width=None, height=32, on=PANEL):
        """A button whose colour fades in on hover. `on` is the colour behind a ghost button."""
        looks = {"primary": (GOLD, GOLD_HOT, ON_GOLD), "quiet": (PANEL2, HOVER, TEXT),
                 "ghost": (on, PANEL2, MUTED), "danger": (DANGER_DIM, "#5a2431", DANGER_TEXT)}
        base, hot, fg = looks[kind]
        kw = dict(text=text, command=self._snd(cmd), corner_radius=9, height=height, fg_color=base, hover=False,
                  text_color=fg, font=F(13, kind == "primary"))
        if width:
            kw["width"] = width
        b = ctk.CTkButton(parent, **kw)
        b.fx = Hover(self, b, base, hot, lambda c: b.configure(fg_color=c))
        return b

    def _label(self, parent, text, size=12, color=MUTED, bold=False, head=False, **kw):
        return ctk.CTkLabel(parent, text=text, text_color=color, font=F(size, bold, head=head), **kw)

    def _switch(self, parent, var):
        def cmd():
            self._click()
            self._sync()
        return ctk.CTkSwitch(parent, text="", variable=var, command=cmd, width=40, switch_width=40, switch_height=21,
                             progress_color=GREEN, fg_color=BORDER, button_color=TEXT, button_hover_color="#ffffff")

    def _page(self, key, title=None, sub=None):
        """A page in the main area. Returns (body, head); head is the title row, where a page can add buttons."""
        page = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        self.pages[key] = page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(page, fg_color="transparent")
        if title:
            head.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 12))
            head.grid_columnconfigure(0, weight=1)
            self._label(head, title, 22, TEXT, True, head=True, anchor="w").grid(row=0, column=0, sticky="w")
            if sub:
                self._label(head, sub, 12, MUTED, anchor="w", height=18).grid(row=1, column=0, sticky="w")
        body = ctk.CTkFrame(page, fg_color=BG, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=22, pady=(0 if title else 18, 8))
        return body, head

    def show_page(self, name):
        self.page = name
        for k, pg in self.pages.items():
            if k == name:
                pg.grid()
            else:
                pg.grid_remove()
        for k, (b, bar, ic, _badge) in self.nav.items():
            on = k == name
            b.configure(text_color=TEXT if on else MUTED, image=self._icon(ic, TEXT if on else MUTED, 16))
            b.fx.set(PANEL2 if on else SIDEBAR, PANEL2 if on else PANEL)
            bar.configure(fg_color=GOLD if on else SIDEBAR)
        if name == "stats":
            self.refresh_stats()

    def _hover_tick(self):
        now = time.time()
        if self.settings.get("sound_ui", True) and now - self._last_tick > 0.06:
            self._last_tick = now
            play_hover()

    def _icon(self, name, color, px=18):
        key = (name, color, px)
        if key not in self._icons:
            im = icon_image(name, color, px)
            self._icons[key] = ctk.CTkImage(light_image=im, dark_image=im, size=(px, px))
        return self._icons[key]

    def _nav_paint(self, key, color):
        b, _bar, _ic, badge = self.nav[key]
        b.configure(fg_color=color)
        badge.configure(bg_color=color)

    def _set_badge(self, key, text, color=MUTED):
        badge = self.nav[key][3]
        if (text, color) == getattr(badge, "shown", None):
            return
        badge.shown = (text, color)
        if text:
            badge.configure(text=f" {text} ", text_color=color, fg_color=CHIP_BG.get(color, PANEL2))
            badge.place(relx=1.0, x=-9, rely=0.5, anchor="e")
        else:
            badge.place_forget()

    def _meter(self, key, text, frac):
        m = self.meters[key]
        if m[2] != text:
            m[2] = text
            m[0].configure(text=text)
        m[1].set(min(1.0, max(0.0, frac)))

    def _paint_history(self, per_day, recent, tcount, ycount, total, n_accounts):
        """The chips on the number tiles, the 7-day bars and the short list of latest secrets."""
        if tcount > ycount:
            note, color = f"+{tcount - ycount} vs yesterday", GREEN
        elif tcount < ycount:
            note, color = f"{tcount - ycount} vs yesterday", MUTED
        else:
            note, color = ("same as yesterday" if tcount else ""), MUTED
        self.big["today"].set(tcount, note, color, hot=tcount > 0)
        self.big["session"].set(sum(self.session.values()),
                                f"{len(self.session)} account{'' if len(self.session) == 1 else 's'}" if self.session else "")
        self.big["total"].set(total, f"{n_accounts} account{'' if n_accounts == 1 else 's'}" if n_accounts else "")
        self._set_badge("stats", str(tcount) if tcount else "", GOLD)
        sig = (tuple(per_day.items()), tuple((h["t"], h["account"]) for h in recent), int(time.time() // 60))
        if sig == getattr(self, "_hist_sig", None):
            return
        self._hist_sig = sig
        top = max(list(per_day.values()) + [1])
        days = list(per_day.items())
        for (pb, num, day), (d, c) in zip(self.week, days):
            today = d == days[-1][0]
            pb.set(max(0.06, c / top) if c else 0)
            pb.configure(progress_color=GOLD if today else "#565d86")
            num.configure(text=str(c) if c else "", text_color=GOLD if today else MUTED)
            day.configure(text=d.strftime("%a")[:2], text_color=TEXT if today else FAINT)
        week = sum(per_day.values())
        self.week_total.configure(text=f"{week} secret{'' if week == 1 else 's'}")
        self.latest_pad.pack_forget()
        for (row, name, when), h in zip(self.latest, list(reversed(recent)) + [None] * len(self.latest)):
            if h is None:
                row.pack_forget()
            else:
                name.configure(text=str(h["account"])[:18])
                when.configure(text=ago(h["t"]))
                row.pack(fill="x", padx=12, pady=0)
        if recent:
            self.latest_empty.pack_forget()
            self.latest_pad.pack()
        else:
            self.latest_empty.pack(fill="x", padx=12, pady=(0, 10))

    def _build_ui(self):
        global HOVER_PATH
        HOVER_PATH = make_hover_sound()
        self._state_sig, self._sum_sig, self._cols, self._card_w = None, None, 0, 236
        self._notes, self._note_w, self._last_w = [], 270, None
        self._icons, self._last_tick, self.side, self.meters = {}, 0.0, {}, {}
        self.started_at = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        def egg(fill, ring, px=66, glow=None):
            im = egg_image(fill, ring, px, glow)
            return ctk.CTkImage(light_image=im, dark_image=im, size=(px, px))
        self._egg = {"idle": egg(PANEL2, "#454a66", glow="#5a6080"), "watch": egg(STAGE, GREEN, glow=GREEN),
                     "secret": egg(GOLD, GOLD_HOT, glow=GOLD), "secret2": egg(GOLD_HOT, "#fff1c9", glow=GOLD_HOT),
                     "empty": egg(PANEL, BORDER, 76)}

        # ---- left sidebar: pages grouped by what they are for, then this session at a glance
        sb = ctk.CTkFrame(self, fg_color=SIDEBAR, corner_radius=0, width=214)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        sb.grid_rowconfigure(30, weight=1)
        sb.grid_columnconfigure(0, weight=1)
        hdr = ctk.CTkFrame(sb, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(18, 12))
        try:
            logo = Image.open(resource_path("icon.png"))
            self.logo_img = ctk.CTkImage(light_image=logo, dark_image=logo, size=(36, 36))
            ctk.CTkLabel(hdr, image=self.logo_img, text="").grid(row=0, column=0, rowspan=2, padx=(0, 10))
        except Exception:
            pass
        self._label(hdr, "Secret Hatch", 16, TEXT, True, head=True, height=20).grid(row=0, column=1, sticky="sw")
        self._label(hdr, "Notifer+", 11, MUTED, height=15).grid(row=1, column=1, sticky="nw")
        r = 1
        for section, items in (("Monitor", (("workspace", "Accounts", "grid"), ("activity", "Activity", "list"))),
                               ("History", (("stats", "Stats", "bars"),)),
                               ("App", (("settings", "Settings", "sliders"),))):
            self._label(sb, section, 11, FAINT, anchor="w", height=16).grid(row=r, column=0, sticky="w", padx=21,
                                                                           pady=(10, 3))
            r += 1
            for key, text, ic in items:
                row = ctk.CTkFrame(sb, fg_color="transparent")
                row.grid(row=r, column=0, sticky="ew", padx=(0, 12), pady=1)
                row.grid_columnconfigure(1, weight=1)
                r += 1
                bar = ctk.CTkFrame(row, fg_color=SIDEBAR, width=3, height=18, corner_radius=2)
                bar.grid(row=0, column=0, padx=(0, 9))
                b = ctk.CTkButton(row, text="  " + text, image=self._icon(ic, MUTED, 16), compound="left", anchor="w",
                                  height=34, corner_radius=9, fg_color=SIDEBAR, hover=False, text_color=MUTED,
                                  font=F(13), command=self._snd(lambda k=key: self.show_page(k)))
                b.grid(row=0, column=1, sticky="ew")
                b.fx = Hover(self, b, SIDEBAR, PANEL, lambda c, k=key: self._nav_paint(k, c))
                badge = ctk.CTkLabel(row, text="", text_color=MUTED, fg_color=PANEL2, bg_color=SIDEBAR,
                                     corner_radius=8, height=18, font=F(10, True))
                badge.bind("<Button-1>", lambda e, k=key: self.show_page(k))
                self.nav[key] = (b, bar, ic, badge)

        ses = ctk.CTkFrame(sb, fg_color=PANEL, border_width=1, border_color=BORDER, corner_radius=12)
        ses.grid(row=20, column=0, sticky="ew", padx=14, pady=(18, 0))
        sh = ctk.CTkFrame(ses, fg_color="transparent")
        sh.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(sh, text="", image=self._icon("clock", MUTED, 14), width=16).pack(side="left")
        self._label(sh, "This session", 12, TEXT, True, height=18).pack(side="left", padx=(7, 0))
        for key, text in (("up", "Running"), ("found", "Secrets found"), ("warn", "Warnings sent"),
                          ("fail", "Alerts failed")):
            row = ctk.CTkFrame(ses, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=1)
            self._label(row, text, 12, MUTED, height=20).pack(side="left")
            self.side[key] = self._label(row, "0", 12, TEXT, True, height=20)
            self.side[key].pack(side="right")
        row.pack_configure(pady=(1, 10))

        # what still has to happen before alerts can go out
        chk = ctk.CTkFrame(sb, fg_color=PANEL, border_width=1, border_color=BORDER, corner_radius=12)
        chk.grid(row=21, column=0, sticky="ew", padx=14, pady=(10, 0))
        ch = ctk.CTkFrame(chk, fg_color="transparent")
        ch.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(ch, text="", image=self._icon("eye", MUTED, 14), width=16).pack(side="left")
        self._label(ch, "Ready check", 12, TEXT, True, height=18).pack(side="left", padx=(7, 0))
        self.checks = {}
        for key, text in (("id", "Discord ID added"), ("win", "Roblox open"), ("run", "Watching")):
            row = ctk.CTkFrame(chk, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=1)
            dot = ctk.CTkLabel(row, text="●", text_color=FAINT, font=F(9), width=12, height=20)
            dot.pack(side="left")
            lb = self._label(row, text, 12, MUTED, height=20)
            lb.pack(side="left", padx=(6, 0))
            self.checks[key] = [dot, lb, None]
        row.pack_configure(pady=(1, 10))

        self.state_pill = ctk.CTkLabel(sb, text="●  Stopped", text_color=MUTED, fg_color=PANEL, corner_radius=10,
                                       height=34, font=F(12, True))
        self.state_pill.grid(row=31, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.update_btn = ctk.CTkButton(sb, text="", corner_radius=9, height=34, fg_color=GOLD, hover_color=GOLD_HOT,
                                        text_color=ON_GOLD, font=F(12, True), command=self.on_update_click)
        self.update_btn.grid(row=32, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.update_btn.grid_remove()
        self._label(sb, f"Version {APP_VERSION}", 11, FAINT).grid(row=33, column=0, sticky="w", padx=20, pady=(0, 12))

        # ---- pages, with the latest activity line always visible underneath
        self.content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.foot = ctk.CTkButton(self.content, text="", anchor="w", height=28, corner_radius=0, fg_color=SIDEBAR,
                                  hover_color=PANEL, text_color=MUTED, font=F(12),
                                  image=self._icon("pulse", FAINT, 14), compound="left",
                                  command=lambda: self.show_page("activity"))
        self.foot.grid(row=1, column=0, sticky="ew")
        self._build_workspace(*self._page("workspace"))
        self._build_stats(*self._page("stats", "Stats", "Real secrets only. Test alerts are not counted."))
        self._build_activity(*self._page("activity", "Activity", "Everything the app has done since you opened it."))
        self._build_settings(*self._page("settings", "Settings", "Changes are saved as you make them."))

        sc = ctk.ScalingTracker.get_widget_scaling(self)
        style = ttk.Style()
        style.theme_use("clam")
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no frame or focus ring around the table
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=int(29 * sc),
                        borderwidth=0, font=(FONT, 10))
        style.configure("Treeview.Heading", background=PANEL, foreground=MUTED, relief="flat", borderwidth=0,
                        padding=(4, 6), font=(FONT, 9))
        style.map("Treeview.Heading", background=[("active", PANEL)])
        style.map("Treeview", background=[("selected", HOVER)], foreground=[("selected", TEXT)])
        self.show_page("workspace")

    # ---- Accounts page: status and start button on top, numbers, then one card per Roblox window
    def _build_workspace(self, body, _head):
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)
        self.ws_body = body

        hero = self._card(body)
        hero.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        hero.grid_columnconfigure(1, weight=1)
        self.egg_lbl = ctk.CTkLabel(hero, text="", image=self._egg["idle"], width=64, height=64)
        self.egg_lbl.grid(row=0, column=0, rowspan=2, padx=(16, 12), pady=12)
        self.hero_title = self._label(hero, "Not watching", 21, TEXT, True, head=True, anchor="w", justify="left",
                                      height=26, wraplength=330)
        self.hero_title.grid(row=0, column=1, sticky="sw", pady=(13, 0))
        self.hero_sub = self._label(hero, "", 12, MUTED, anchor="w", justify="left", wraplength=330)
        self.hero_sub.grid(row=1, column=1, sticky="nw", pady=(1, 13))

        idbox = ctk.CTkFrame(hero, fg_color="transparent")
        idbox.grid(row=0, column=2, rowspan=2, padx=(14, 14), pady=8)
        self._label(idbox, "Your Discord user ID", 11, MUTED, anchor="w", height=16).grid(row=0, column=0, sticky="w", padx=2)
        self.id_var = ctk.StringVar(value=self.cfg["discord_id"])
        self.id_entry = ctk.CTkEntry(idbox, textvariable=self.id_var, fg_color=BG, border_color=BORDER,
                                     border_width=2, text_color=TEXT, height=36, width=204, corner_radius=9,
                                     font=F(13))
        self.id_entry.grid(row=1, column=0, pady=(1, 1))
        self.id_entry.bind("<Return>", lambda e: None if self.running else self.on_toggle())
        self.id_var.trace_add("write", lambda *a: self._id_error(False))
        self.start_btn = self._btn(idbox, "Start watching", self.on_toggle, "primary", width=148, height=36)
        self.start_btn.grid(row=1, column=1, padx=(8, 0), pady=(1, 1))
        self.id_help_btn = self._btn(idbox, "Where do I find my ID?", self._toggle_id_help, "ghost", width=10, height=22)
        self.id_help_btn.configure(anchor="w", font=F(11), corner_radius=6)
        self.id_help_btn.grid(row=2, column=0, sticky="w")
        self.id_err = self._label(idbox, "Needs 17 to 20 digits, numbers only.", 11, DANGER_TEXT, anchor="w", height=22)
        self.id_err.grid(row=2, column=0, sticky="w", padx=4)
        self.id_err.grid_remove()
        tb = self._btn(idbox, "Send a test alert", self.on_test_alert, "ghost", width=148, height=22)
        tb.configure(font=F(11), corner_radius=6)
        tb.grid(row=2, column=1, padx=(8, 0))
        self.id_help = self._label(
            hero, "In Discord, open Settings, then Advanced, and switch on Developer Mode. After that, right-click your "
                  "own name anywhere and choose Copy User ID. The ID is only used to ping you.",
            12, MUTED, anchor="w", justify="left", wraplength=760)
        self.id_help.grid(row=2, column=0, columnspan=3, sticky="w", padx=18, pady=(0, 12))
        self.id_help.grid_remove()

        # four numbers, each with a chip that puts it in context
        tiles = ctk.CTkFrame(body, fg_color="transparent")
        tiles.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.big = {}
        for i, (key, icon, title) in enumerate((("today", "egg", "Secrets today"), ("session", "pulse", "This session"),
                                                ("total", "bars", "All time"), ("watch", "eye", "Watching"))):
            tiles.grid_columnconfigure(i, weight=1, uniform="tile")
            self.big[key] = Tile(tiles, self, icon, title)
            self.big[key].grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 5, 0 if i == 3 else 5))

        # left: the accounts
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=2, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        self.ws_left = left
        hd = ctk.CTkFrame(left, fg_color="transparent")
        hd.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        hd.grid_columnconfigure(0, weight=1)
        self._label(hd, "Accounts", 17, TEXT, True, head=True, anchor="w", height=22).grid(row=0, column=0, sticky="w")
        self.summary = self._label(hd, "", 12, MUTED, anchor="w", height=16)
        self.summary.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        bar = ctk.CTkFrame(hd, fg_color="transparent")
        bar.grid(row=0, column=1, sticky="e")
        self._btn(bar, "Find windows", self.scan_windows, height=28, width=104).pack(side="left", padx=(0, 5))
        self._btn(bar, "Watch all", lambda: self._set_all(True), height=28, width=84).pack(side="left", padx=(0, 5))
        self._btn(bar, "Watch none", lambda: self._set_all(False), height=28, width=92).pack(side="left")
        self.grid_area = ctk.CTkScrollableFrame(left, fg_color=BG, corner_radius=0,
                                                scrollbar_button_color=PANEL2, scrollbar_button_hover_color=HOVER)
        self.grid_area.grid(row=1, column=0, sticky="nsew")
        self.empty_box = ctk.CTkFrame(self.grid_area, fg_color="transparent")
        ctk.CTkLabel(self.empty_box, text="", image=self._egg["empty"]).pack(pady=(34, 8))
        self._label(self.empty_box, "No Roblox windows open", 16, TEXT, True, head=True).pack()
        self._label(self.empty_box, "Open Roblox and each window shows up here on its own.", 12, MUTED).pack(pady=(2, 0))
        self.empty_box.grid(row=0, column=0, sticky="ew")
        self.grid_area.grid_columnconfigure(0, weight=1)

        # right: the last week, this PC, and the latest secrets
        rail = ctk.CTkFrame(body, fg_color="transparent", width=248)
        rail.grid(row=2, column=1, sticky="ns", padx=(12, 0))
        rail.grid_propagate(False)
        rail.grid_columnconfigure(0, weight=1)

        def panel(row, icon, title):
            c = self._card(rail)
            c.grid(row=row, column=0, sticky="ew", pady=(0, 10))
            h = ctk.CTkFrame(c, fg_color="transparent")
            h.pack(fill="x", padx=12, pady=(10, 6))
            ctk.CTkLabel(h, text="", image=self._icon(icon, MUTED, 14), width=16).pack(side="left")
            self._label(h, title, 12, TEXT, True, height=18).pack(side="left", padx=(7, 0))
            return c, h

        wk, wh = panel(0, "calendar", "Last 7 days")
        self.week_total = self._label(wh, "", 11, MUTED, height=18)
        self.week_total.pack(side="right")
        cols = ctk.CTkFrame(wk, fg_color="transparent")
        cols.pack(fill="x", padx=10, pady=(0, 10))
        self.week = []
        for i in range(7):
            cols.grid_columnconfigure(i, weight=1, uniform="day")
            num = self._label(cols, "", 10, FAINT, height=14)
            num.grid(row=0, column=i)
            pb = ctk.CTkProgressBar(cols, orientation="vertical", width=12, height=52, corner_radius=5,
                                    fg_color=PANEL2, progress_color=HOVER, border_width=0)
            pb.set(0)
            pb.grid(row=1, column=i, pady=2)
            day = self._label(cols, "", 10, FAINT, height=14)
            day.grid(row=2, column=i)
            self.week.append((pb, num, day))

        pc, _ = panel(1, "pc", "This PC")
        for key, title in (("pc_rob", "Roblox memory"), ("pc_free", "Memory in use"), ("pc_cpu", "Processor")):
            row = ctk.CTkFrame(pc, fg_color="transparent")
            row.pack(fill="x", padx=12)
            self._label(row, title, 11, MUTED, height=18).pack(side="left")
            val = self._label(row, "", 11, TEXT, True, height=18)
            val.pack(side="right")
            pb = ctk.CTkProgressBar(pc, height=4, corner_radius=2, fg_color=PANEL2, progress_color="#7c86b8",
                                    border_width=0)
            pb.set(0)
            pb.pack(fill="x", padx=12, pady=(1, 7))
            self.meters[key] = [val, pb, None]
        ctk.CTkFrame(pc, fg_color="transparent", height=3).pack()

        ls, _ = panel(2, "egg", "Latest secrets")
        self.latest_empty = self._label(ls, "None yet. The next one lands here.", 11, FAINT, anchor="w", height=18)
        self.latest_empty.pack(fill="x", padx=12, pady=(0, 10))
        self.latest = []
        for _ in range(4):
            row = ctk.CTkFrame(ls, fg_color="transparent")
            ctk.CTkLabel(row, text="●", text_color=GOLD, font=F(8), width=10, height=20).pack(side="left")
            name = self._label(row, "", 12, TEXT, anchor="w", height=20)
            name.pack(side="left", padx=(6, 0))
            when = self._label(row, "", 11, FAINT, height=20)
            when.pack(side="right")
            self.latest.append((row, name, when))
        self.latest_pad = ctk.CTkFrame(ls, fg_color="transparent", height=8)
        body.bind("<Configure>", self._on_resize)

    def _toggle_id_help(self):
        if self.id_help.winfo_ismapped():
            self.id_help.grid_remove()
        else:
            self.id_help.grid()

    def _id_error(self, on):
        if on == bool(self.id_err.winfo_manager()):
            return
        if on:
            self.id_help_btn.grid_remove()
            self.id_err.grid()
            self.id_entry.configure(border_color=DANGER)
            self.show_page("workspace")
            self.id_entry.focus_set()
        else:
            self.id_err.grid_remove()
            self.id_help_btn.grid()
            self.id_entry.configure(border_color=BORDER)

    def _on_resize(self, event=None):
        """Works out how many account cards fit side by side and how wide each one should be."""
        try:
            sc = ctk.ScalingTracker.get_widget_scaling(self)
            bw = self.ws_body.winfo_width() / sc
            w = self.ws_left.winfo_width() / sc - 26  # room for the scrollbar
        except Exception:
            return
        if w < 150 or (w, bw) == self._last_w:
            return
        self._last_w = (w, bw)
        gap = 10
        cols = max(1, int((w + gap) // (196 + gap)))
        card_w = min(330, int((w - gap * (cols - 1)) / cols))
        self.hero_sub.configure(wraplength=max(220, int(bw) - 500))
        self.hero_title.configure(wraplength=max(220, int(bw) - 500))
        self.id_help.configure(wraplength=max(300, int(bw) - 40))
        note_w = max(220, int(bw / 2) - 150)
        if abs(note_w - self._note_w) >= 8:
            self._note_w = note_w
            for lb in self._notes:
                lb.configure(wraplength=note_w)
        if cols != self._cols or abs(card_w - self._card_w) >= 3:
            self._cols, self._card_w = cols, card_w
            self._layout_cards()

    def _build_activity(self, body, head):
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        bar = ctk.CTkFrame(head, fg_color="transparent")
        bar.grid(row=0, column=1, rowspan=2, sticky="e")
        self._btn(bar, "Copy all", self.on_copy_log).pack(side="left", padx=(0, 6))
        self._btn(bar, "Clear", self.on_clear_log).pack(side="left")
        lg = self._card(body)
        lg.grid(row=0, column=0, sticky="nsew")
        self.logbox = ctk.CTkTextbox(lg, fg_color=PANEL, text_color=TEXT, border_width=0, corner_radius=12,
                                     font=F(13, mono=True), spacing1=3, spacing3=3, wrap="word",
                                     scrollbar_button_color=PANEL2, scrollbar_button_hover_color=HOVER)
        self.logbox.pack(fill="both", expand=True, padx=10, pady=10)
        for tag, color in (("dim", FAINT), ("gold", GOLD), ("bad", DANGER_TEXT), ("ok", GREEN)):
            self.logbox.tag_config(tag, foreground=color)
        self.logbox.configure(state="disabled")

    def on_copy_log(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self.logbox.get("1.0", "end").strip())
            self.log("Activity copied to the clipboard.")
        except Exception as e:
            self.log(f"Couldn't copy the activity: {e}")

    def on_clear_log(self):
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")

    # ---- Settings
    def _setting(self, card, row, title, note, make):
        box = ctk.CTkFrame(card, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", padx=20, pady=8)
        box.grid_columnconfigure(0, weight=1)
        self._label(box, title, 14, TEXT, anchor="w", height=20).grid(row=0, column=0, sticky="w")
        if note:
            lb = self._label(box, note, 12, MUTED, anchor="w", justify="left", wraplength=270)
            lb.grid(row=1, column=0, sticky="w")
            self._notes.append(lb)
        make(box).grid(row=0, column=1, rowspan=2, sticky="e", padx=(14, 0))

    def _build_settings(self, body, _head):
        body.grid_columnconfigure((0, 1), weight=1, uniform="s")
        self.cool_var = ctk.StringVar(value=str(self.cfg["rearm"]))
        self.int_var = ctk.StringVar(value=str(self.cfg["interval"]))
        self.peek_var = ctk.StringVar(value=str(self.cfg["peek"]))
        self.rev_var = ctk.StringVar(value=str(self.cfg["reveal_delay"]))
        self.min_var = ctk.BooleanVar(value=self.cfg["minimized"])
        self.full_var = ctk.BooleanVar(value=False)
        self.dc_var = ctk.BooleanVar(value=self.cfg["dc_warn"])
        self.rev_pic_var = ctk.BooleanVar(value=self.cfg["reveal_pic"])
        self.sound_ui_var = ctk.BooleanVar(value=self.cfg["sound_ui"])
        self.sound_alert_var = ctk.BooleanVar(value=self.cfg["sound_alert"])

        def seconds(var):
            def make(box):
                f = ctk.CTkFrame(box, fg_color="transparent")
                ctk.CTkEntry(f, textvariable=var, fg_color=BG, border_color=BORDER, border_width=2, height=34,
                             width=64, corner_radius=8, justify="center", font=F(14)).pack(side="left")
                self._label(f, "sec", 12, MUTED).pack(side="left", padx=(8, 0))
                return f
            return make

        def switch(var):
            return lambda box: self._switch(box, var)

        tm = self._card(body)
        tm.grid(row=0, column=0, sticky="nsew", padx=(0, 7), pady=(0, 14))
        tm.grid_columnconfigure(0, weight=1)
        self._label(tm, "Timing", 17, TEXT, True, head=True, anchor="w").grid(row=0, column=0, sticky="w", padx=20, pady=(16, 4))
        for i, (title, note, var) in enumerate((
                ("Check each window every", "Lower is faster but uses more of your PC. 0.2 or more.", self.int_var),
                ("Check minimized windows every", "Minimized windows are slower to read. 2 or more.", self.peek_var),
                ("Wait before the next alert", "How long a secret must be off screen before the same account "
                                                "can alert again. 3 or more.", self.cool_var),
                ("Take the second picture after", "Only used when the second picture is switched on.", self.rev_var))):
            self._setting(tm, 1 + i, title, note, seconds(var))
        ctk.CTkLabel(tm, text="", height=6).grid(row=20, column=0)

        op = self._card(body)
        op.grid(row=0, column=1, sticky="nsew", padx=(7, 0), pady=(0, 14))
        op.grid_columnconfigure(0, weight=1)
        self._label(op, "Alerts and sound", 17, TEXT, True, head=True, anchor="w").grid(row=0, column=0, sticky="w", padx=20, pady=(16, 4))
        for i, (title, note, var) in enumerate((
                ("Watch minimized windows", "Reads them in the background without bringing them up.", self.min_var),
                ("Warn me when an account disconnects", "Pings you if Roblox shows an error screen or a window "
                                                        "closes.", self.dc_var),
                ("Send a second picture after the reveal", "Shows what actually hatched.", self.rev_pic_var),
                ("Play a sound when a secret is found", "", self.sound_alert_var),
                ("Button sounds", "A soft tick on hover and a click on press.", self.sound_ui_var))):
            self._setting(op, 1 + i, title, note, switch(var))
        self._btn(op, "Play the sound", play_sound, width=120).grid(row=19, column=0, sticky="w", padx=20, pady=(4, 16))

        ab = self._card(body)
        ab.grid(row=1, column=0, columnspan=2, sticky="ew")
        ab.grid_columnconfigure(0, weight=1)
        self._label(ab, f"Secret Hatch Notifer+ {APP_VERSION}", 17, TEXT, True, head=True, anchor="w"
                    ).grid(row=0, column=0, sticky="w", padx=20, pady=(16, 0))
        self._label(ab, "Looks for a new version when it starts and every 6 hours. When one is out, an Update button "
                        "appears at the bottom left.", 12, MUTED, anchor="w", justify="left", wraplength=420
                    ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))
        btns = ctk.CTkFrame(ab, fg_color="transparent")
        btns.grid(row=0, column=1, rowspan=2, sticky="e", padx=20)
        self._btn(btns, "Check for updates", lambda: self.check_updates(True)).pack(side="left", padx=(0, 6))
        self._btn(btns, "Open data folder", self.on_open_data).pack(side="left")

    def on_open_data(self):
        try:
            os.startfile(APP_DIR)
        except Exception as e:
            self.log(f"Couldn't open the folder: {e}")

    # ------------------------------------------------------------- logging
    def log(self, msg):
        self.log_q.put(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _drain(self):
        lines = []
        try:
            while True:
                lines.append(self.log_q.get_nowait())
        except queue.Empty:
            pass
        if not lines:
            return
        self.logbox.configure(state="normal")
        tag = msg = ""
        for ln in lines:
            stamp, _, msg = ln.partition("] ")
            low = msg.lower()
            if any(k in low for k in ("fail", "couldn't", "can't", "error", "enter a valid")):
                tag = "bad"
            elif "secret detected" in low or "new update" in low:
                tag = "gold"
            elif " sent" in low:
                tag = "ok"
            else:
                tag = ""
            self.logbox.insert("end", stamp[1:] + "   ", "dim")
            self.logbox.insert("end", msg + "\n", tag or None)
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        colors = {"bad": DANGER_TEXT, "gold": GOLD, "ok": GREEN}
        self.foot.configure(text="  " + msg.replace("\n", " ")[:150], text_color=colors.get(tag, MUTED))

    # ------------------------------------------------------------ settings
    def _num(self, var, default, lo):
        try:
            v = max(lo, float(var.get()))
            return int(v) if v == int(v) else v
        except Exception:
            return default

    def _sync(self):
        snap = {"discord_id": self.id_var.get().strip(), "rearm": self._num(self.cool_var, 6, 3),
                "interval": self._num(self.int_var, 1.0, 0.2), "peek": self._num(self.peek_var, 5, 2),
                "minimized": bool(self.min_var.get()), "fullsize": bool(self.full_var.get()), "dc_warn": bool(self.dc_var.get()), "reveal_pic": bool(self.rev_pic_var.get()),
                "sound_ui": bool(self.sound_ui_var.get()), "sound_alert": bool(self.sound_alert_var.get()),
                "reveal_delay": self._num(self.rev_var, 8, 1)}
        self.settings = snap
        if snap != self._last_saved:
            self._last_saved = dict(snap)
            save_config(snap)

    def _valid_id(self):
        self._sync()
        if not ID_RE.match(self.settings["discord_id"]):
            self.log("Enter a valid Discord user ID first (17-20 digits).")
            self._id_error(True)
            return False
        self._id_error(False)
        return True

    def on_toggle(self):
        if not self.running and not self._valid_id():
            return
        self.running = not self.running
        self.started_at = time.time() if self.running else None
        for st in self.states.values():
            st.armed, st.streak, st.gone_since, st.hit = True, 0, None, False
        self.log("Watching started." if self.running else "Watching stopped.")
        self._refresh_table()

    # ------------------------------------------------------------- windows
    # ---------------------------------------------------------------- stats
    def _build_stats(self, tab, head):
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(0, weight=1)
        bar = ctk.CTkFrame(head, fg_color="transparent")
        bar.grid(row=0, column=1, rowspan=2, sticky="e")
        self._btn(bar, "Export as CSV", self.on_export_stats).pack(side="left", padx=(0, 6))
        self._btn(bar, "Reset session", self.on_reset_session).pack(side="left", padx=(0, 6))
        self._btn(bar, "Clear history", self.on_clear_stats, "danger").pack(side="left")

        def table(parent, cols, widths, title):
            card = self._card(parent)
            card.grid_columnconfigure(0, weight=1)
            card.grid_rowconfigure(1, weight=1)
            self._label(card, title, 17, TEXT, True, head=True, anchor="w").grid(row=0, column=0, sticky="w", padx=20, pady=(16, 6))
            tv = ttk.Treeview(card, columns=[c[0] for c in cols], show="headings", height=10, selectmode="none")
            for (cid, name, anchor), w in zip(cols, widths):
                tv.heading(cid, text=name, anchor=anchor)
                tv.column(cid, width=w, anchor=anchor, stretch=(cid == cols[0][0]))
            tv.tag_configure("alt", background="#1e202c")
            tv.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 14))
            return card, tv

        c1, self.acct_tree = table(tab, (("a", "Account", "w"), ("t", "Today", "e"), ("s", "Session", "e"),
                                         ("all", "All time", "e"), ("l", "Last secret", "e")),
                                   (150, 70, 70, 70, 140), "By account")
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        c2, self.recent_tree = table(tab, (("when", "When", "w"), ("acc", "Account", "w")), (170, 130), "Latest secrets")
        c2.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self.session_note = self._label(tab, "", 12, MUTED, anchor="w")
        self.session_note.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def record_hatch(self, label):
        with self.stats_lock:
            self.stats["history"].append({"t": time.time(), "account": label})
            self.stats["history"] = self.stats["history"][-2000:]
            self.stats["total"] += 1
            self.stats["accounts"][label] = self.stats["accounts"].get(label, 0) + 1
            save_stats(self.stats)
            self.session[label] = self.session.get(label, 0) + 1
            today = datetime.date.today()
            tc = sum(1 for h in self.stats["history"] if datetime.datetime.fromtimestamp(h["t"]).date() == today)
            return {"today": tc, "total": self.stats["total"], "account_total": self.stats["accounts"][label],
                    "session": sum(self.session.values())}

    def _today_count(self):
        today = datetime.date.today()
        with self.stats_lock:
            return sum(1 for h in self.stats["history"] if datetime.datetime.fromtimestamp(h["t"]).date() == today)

    def refresh_stats(self):
        with self.stats_lock:
            hist, accts, total = list(self.stats["history"]), dict(self.stats["accounts"]), self.stats["total"]
        today = datetime.date.today()
        per_day = {today - datetime.timedelta(days=i): 0 for i in range(6, -1, -1)}
        tcount, per_today, last = 0, {}, {}
        for h in hist:
            d = datetime.datetime.fromtimestamp(h["t"])
            last[h["account"]] = d
            if d.date() in per_day:
                per_day[d.date()] += 1
            if d.date() == today:
                tcount += 1
                per_today[h["account"]] = per_today.get(h["account"], 0) + 1
        self._paint_history(per_day, hist[-len(self.latest):], tcount,
                            per_day[today - datetime.timedelta(days=1)], total, len(accts))
        if self.page != "stats":
            return
        names = sorted(set(accts) | set(self.session) | {x.label for x in self.states.values()})
        self.acct_tree.delete(*self.acct_tree.get_children())
        for i, n in enumerate(names):
            l = last.get(n)
            self.acct_tree.insert("", "end", tags=("alt",) if i % 2 else (),
                                  values=(n, per_today.get(n, 0), self.session.get(n, 0), accts.get(n, 0),
                                          l.strftime("%b %d, %I:%M %p") if l else "never"))
        self.recent_tree.delete(*self.recent_tree.get_children())
        for i, h in enumerate(reversed(hist[-60:])):
            self.recent_tree.insert("", "end", tags=("alt",) if i % 2 else (),
                                    values=(datetime.datetime.fromtimestamp(h["t"]).strftime("%b %d, %I:%M:%S %p"),
                                            h["account"]))

    def on_export_stats(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="secret_hatches.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with self.stats_lock:
                hist = list(self.stats["history"])
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write("time,account\n")
                for h in hist:
                    f.write(f'{datetime.datetime.fromtimestamp(h["t"]).isoformat(sep=" ", timespec="seconds")},"{h["account"]}"\n')
            self.log(f"Exported {len(hist)} rows to {path}")
        except Exception as e:
            self.log(f"Export failed: {e}")

    def on_reset_session(self):
        self.session = {}
        self.refresh_stats()
        self.log("Session counter reset.")

    def on_clear_stats(self):
        if messagebox.askyesno("Clear secret history", "Delete your whole secret history and reset ALL counts (today, session and all-time)? This can't be undone."):
            with self.stats_lock:
                self.stats = {"total": 0, "accounts": {}, "history": []}
                save_stats(self.stats)
            self.session = {}
            self.refresh_stats()
            self.log("Secret history cleared.")

    # ---------------------------------------------------------------- windows
    def scan_windows(self):
        found = find_roblox_windows(self.name_cache)
        live = {h for h, _, _ in found}
        for hwnd, pid, _ in found:
            if hwnd not in self.states:
                self.counter += 1
                self.states[hwnd] = WinState(hwnd, pid, f"Account {self.counter}")
                self.log(f"Found Account {self.counter} (PID {pid}).")
        for hwnd in list(self.states):
            if hwnd not in live:
                gone = self.states[hwnd]
                self.log(f"{gone.label} closed.")
                if self.running and gone.watch and self.settings["dc_warn"]:
                    threading.Thread(target=self._send_warning,
                                     args=(gone.label, "Roblox window closed (crashed or was closed)", None, dict(self.settings)),
                                     daemon=True).start()
                del self.states[hwnd]
                card = self.cards.pop(hwnd, None)
                if card is not None:
                    card.destroy()
                if hwnd in self.card_order:
                    self.card_order.remove(hwnd)
                if self.selected == hwnd:
                    self.selected = None
                self._layout_cards()
        self._refresh_table()

    def _layout_cards(self):
        cols = max(1, self._cols)
        for i, hwnd in enumerate(self.card_order):
            c = self.cards.get(hwnd)
            if c is not None:
                c.set_size(self._card_w)
                c.grid(row=i // cols, column=i % cols, padx=(0, 10), pady=(0, 10), sticky="nw")
        self.grid_area.grid_columnconfigure(0, weight=0 if self.card_order else 1)
        if self.card_order:
            self.empty_box.grid_remove()
        else:
            self.empty_box.grid()

    def _status_of(self, st):
        if not st.watch:
            return "Not watched", MUTED
        if st.hit:
            return "Secret!", GOLD
        if st.dc_streak >= 2:
            return "Disconnected?", DANGER
        if not self.running:
            return "Ready", MUTED
        if st.status.startswith("(min)"):
            return "Minimized", GREEN
        if st.status.startswith("Can't") or st.status.startswith("Error"):
            return st.status[:22], DANGER
        return "Watching", GREEN

    def _refresh_table(self):
        self._on_resize()
        n = secret = dc = mini = 0
        rob = 0
        hot = []
        for hwnd, st in list(self.states.items()):
            if hwnd not in self.cards:
                self.cards[hwnd] = AccountCard(self.grid_area, self, st)
                self.card_order.append(hwnd)
                self._layout_cards()
            mem = ""
            if st.proc is not None:
                try:
                    rss = st.proc.memory_info().rss
                    rob += rss
                    mem = f"{rss / 1048576:.0f} MB"
                except Exception:
                    pass
            text, color = self._status_of(st)
            self.cards[hwnd].refresh(st, mem, text, color, hwnd == self.selected)
            if st.watch:
                n += 1
                if st.hit:
                    secret += 1
                    hot.append(st.label)
                if st.dc_streak >= 2:
                    dc += 1
                if st.status.startswith("(min)"):
                    mini += 1
        total = len(self.states)
        try:
            vm = psutil.virtual_memory()
            self._meter("pc_rob", f"{rob / 1073741824:.1f} GB", rob / vm.total)
            self._meter("pc_free", f"{(vm.total - vm.available) / 1073741824:.1f} of {vm.total / 1073741824:.0f} GB",
                        vm.percent / 100)
            cpu = psutil.cpu_percent(None)
            self._meter("pc_cpu", f"{cpu:.0f}%", cpu / 100)
        except Exception:
            pass
        if not total:
            self.big["watch"].set("0")
        elif n == total:
            self.big["watch"].set(f"{n} of {total}", "all on", GREEN)
        else:
            self.big["watch"].set(f"{n} of {total}", f"{total - n} off")
        self._set_badge("workspace", str(total) if total else "")
        up = fmt_uptime(time.time() - self.started_at) if (self.running and self.started_at) else "stopped"
        for key, val, bad in (("up", up, False), ("found", sum(self.session.values()), False),
                              ("warn", self.dc_count, False), ("fail", self.fail_count, self.fail_count > 0)):
            lb = self.side[key]
            if getattr(lb, "shown", None) != val:
                lb.shown = val
                lb.configure(text=str(val), text_color=DANGER_TEXT if bad else (MUTED if val == "stopped" else TEXT))
        for key, ok in (("id", bool(ID_RE.match(self.settings.get("discord_id", "")))), ("win", total > 0),
                        ("run", self.running and n > 0)):
            c = self.checks[key]
            if c[2] != ok:
                c[2] = ok
                c[0].configure(text_color=GREEN if ok else FAINT)
                c[1].configure(text_color=TEXT if ok else MUTED)
        sig = (n, total, secret, dc, mini, self.dc_count, self.fail_count)
        if sig != self._sum_sig:
            self._sum_sig = sig
            parts = []
            if secret:
                parts.append(f"{secret} with a secret on screen")
            if dc:
                parts.append(f"{dc} may be disconnected")
            if mini:
                parts.append(f"{mini} minimized")
            tail = "Double-click one to open its live view." if total else ""
            self.summary.configure(text=((", ".join(parts)).capitalize() + ".  " if parts else "") + tail)
            self.session_note.configure(
                text=f"Since you opened the app: {self.dc_count} disconnect warning{'' if self.dc_count == 1 else 's'} "
                     f"sent, {self.fail_count} alert{'' if self.fail_count == 1 else 's'} failed to send.")
        self._paint_state(n, total, hot)
        self.title(f"Secret Hatch Notifer+ — watching {n} of {total}  |  secrets today: {self._today_count()}")

    def _paint_state(self, n, total, hot):
        """The big status line, the egg next to it, the start button and the pill in the sidebar."""
        if self.running and hot:
            mode = "secret"
            title = f"Secret on screen: {hot[0][:18]}" if len(hot) == 1 else f"{len(hot)} secrets on screen"
            sub = "Go reveal it. Discord gets one alert per secret."
            pill, pill_color = "●  Secret on screen", GOLD
            self.egg_lbl.configure(image=self._egg["secret2" if self._tick_n % 2 else "secret"])
        elif self.running:
            mode = "watch"
            title = f"Watching {n} account{'' if n == 1 else 's'}" if n else "Nothing to watch"
            sub = (f"Running for {fmt_uptime(time.time() - (self.started_at or time.time()))}. A secret pings you in "
                   "Discord with a picture of that Roblox window." if n else
                   "Open Roblox, or switch an account back on below.")
            pill, pill_color = f"●  Watching {n} of {total}", GREEN
        else:
            mode, title = "idle", "Not watching"
            if not ID_RE.match(self.settings.get("discord_id", "")):
                sub = "Paste your Discord user ID, then start. You get pinged when one of your accounts hatches a secret."
            elif total:
                sub = f"Ready. {total} Roblox window{'' if total == 1 else 's'} found."
            else:
                sub = "Open Roblox first. Its windows show up below on their own."
            pill, pill_color = "●  Stopped", MUTED
        sig = (mode, title, sub, pill)
        if sig == self._state_sig:
            return
        changed_mode = self._state_sig is None or self._state_sig[0] != mode
        self._state_sig = sig
        self.hero_title.configure(text=title, text_color=GOLD if mode == "secret" else TEXT)
        self.hero_sub.configure(text=sub)
        self.state_pill.configure(text=pill, text_color=pill_color)
        if changed_mode:
            if mode != "secret":
                self.egg_lbl.configure(image=self._egg[mode])
            if self.running:
                self.start_btn.configure(text="Stop watching", text_color=TEXT)
                self.start_btn.fx.set(PANEL2, "#5a2431")
            else:
                self.start_btn.configure(text="Start watching", text_color=ON_GOLD)
                self.start_btn.fx.set(GOLD, GOLD_HOT)

    def _pick(self):
        st = self.states.get(self.selected)
        if st is None and len(self.states) == 1:
            st = next(iter(self.states.values()))
        return st

    def _selected(self):
        st = self._pick()
        if st is None:
            self.log("Click an account card first.")
        return st

    def select(self, hwnd):
        self.selected = hwnd
        self._refresh_table()

    def set_watch(self, hwnd, on):
        st = self.states.get(hwnd)
        if st:
            st.watch = on
            st.armed, st.streak, st.gone_since, st.hit = True, 0, None, False
            self._refresh_table()

    def open_view(self, hwnd):
        self.selected = hwnd
        self.on_view()

    def _set_all(self, on):
        for st in self.states.values():
            st.watch = on
            st.armed, st.streak, st.gone_since, st.hit = True, 0, None, False
        self._refresh_table()

    def on_front(self):
        st = self._selected()
        if st:
            bring_to_front(st.hwnd)

    def on_rename(self):
        st = self._selected()
        if not st:
            return
        name = ctk.CTkInputDialog(text=f"New name for {st.label}", title="Rename account", fg_color=BG,
                                  button_fg_color=PANEL2, button_hover_color=HOVER, button_text_color=TEXT,
                                  entry_fg_color=STAGE, entry_border_color=BORDER, entry_text_color=TEXT,
                                  text_color=TEXT).get_input()
        if name and name.strip():
            st.label = name.strip()[:40]
            self._refresh_table()

    def _preview_worker(self):
        """Automatic previews disabled to eliminate background capture."""
        return

    # ----------------------------------------------------------- detection
    def _get_ocr(self):
        with self.ocr_lock:
            if self.ocr is None:
                self.log("Loading text reader (first time only)…")
                from rapidocr_onnxruntime import RapidOCR
                self.ocr = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
            return self.ocr

    def _ocr_text(self, img, target_w):
        if img.width != target_w:
            img = img.resize((target_w, max(1, int(img.height * target_w / img.width))), Image.LANCZOS)
        ocr = self._get_ocr()
        with self.ocr_lock:
            result, _ = ocr(np.asarray(img)[:, :, ::-1])
        return " ".join(r[1] for r in result).strip() if result else ""

    def analyze(self, frame):
        """Read the complete popup banner and small reveal button independently."""
        img = Image.fromarray(frame)
        w, h = img.size
        regions = (
            # The special-hatch banner spans almost the entire bottom edge.
            (img.crop((0, int(h * .78), w, h)), 1280),
            # Enlarge the small outlined button without scaling the whole scene.
            (img.crop((int(w * .30), int(h * .72), int(w * .70), int(h * .91))), 800),
            (img, 1280),  # fallback for alternate window shapes / UI placement
        )
        readings = []
        for region, width in regions:
            text = self._ocr_text(region, width)
            readings.append(text)
            reason = reveal_reason(text)
            if reason:
                return True, reason, text
        return False, "", " ".join(readings).strip()

    def _worker(self):
        grabber = Grabber()
        while not self.stop.is_set():
            watched = [s for s in list(self.states.values()) if s.watch]
            if not self.running or not watched:
                time.sleep(0.4)
                continue
            for st in watched:
                if self.stop.is_set() or not self.running:
                    break
                if not st.watch or st.hwnd not in self.states:
                    continue
                scan_started = time.monotonic()
                try:
                    self._check(st, grabber, self.settings)
                except Exception as e:
                    st.hit, st.status = False, "Error: " + str(e)[:40]
                if self.settings.get("low_cpu", True):
                    # Yield at least as long as this check worked; avoid sustained busy scans.
                    self.stop.wait(max(0.25, time.monotonic() - scan_started))
            time.sleep(self.settings["interval"])

    def _check(self, st, grabber, s):
        # Do not capture/restore windows on passes where no OCR is scheduled.
        # Immediate second-frame confirmation below bypasses this gate.
        scan_gap = max(6.0 if s.get("low_cpu", True) else 3.0, s.get("interval", 1.0))
        if time.time() - st.last_txt < scan_gap:
            return
        if not win32gui.IsWindow(st.hwnd):
            return
        minimized = bool(win32gui.IsIconic(st.hwnd))
        small = False  # small / half-size windows are scanned as they are, never resized
        if minimized and not s["minimized"]:
            st.hit, st.status = False, "Minimized (watching it is off)"
            return
        if (minimized or small) and time.time() - st.last_peek < s["peek"]:
            return
        if minimized or small:
            st.last_peek = time.time()
        frame = grabber.capture(st.hwnd, "Auto", allow_peek=s["minimized"], full=s["fullsize"])
        if frame is None or frame.size == 0:
            st.alert_guard.observe(None, time.monotonic())
            st.hit, st.status = False, "Can't capture this window"
            return
        tag = "(min) " if minimized else ("(big) " if small else "")
        peeked = minimized or small
        now = time.time()
        hit, why = False, ""
        # Only fresh OCR observations advance confirmation or re-arming.
        checked = False
        if now - st.last_txt >= 3.0:
            st.last_txt = now
            try:
                hit, why, _ = self.analyze(frame)
                checked = True
            except Exception:
                st.alert_guard.observe(None, time.monotonic())
                st.status = "Text reader unavailable — alerts paused"
                return

        if not checked:
            return
        send = st.alert_guard.observe(hit, time.monotonic(), s["rearm"])
        if hit and st.alert_guard.armed and st.alert_guard.hits == 1:
            # Confirm this account before moving through the rest of the queue.
            st.hit, st.status = True, "Confirming reveal…"
            started = time.monotonic()
            if self.stop.wait(0.35):
                return
            if not self.running or not st.watch or self.states.get(st.hwnd) is not st:
                return
            try:
                fresh = grabber.capture(st.hwnd, "Auto", allow_peek=s["minimized"], full=s["fullsize"])
                if fresh is None or fresh.size == 0:
                    st.alert_guard.observe(None, time.monotonic())
                    st.hit, st.status = False, "Confirmation capture unavailable"
                    return
                hit, why, _ = self.analyze(fresh)
                frame = fresh
                send = st.alert_guard.observe(hit, time.monotonic(), s["rearm"])
                self.log(f"{st.label}: confirmation {'matched' if hit else 'cleared'} in {time.monotonic() - started:.1f}s.")
            except Exception:
                st.alert_guard.observe(None, time.monotonic())
                st.hit, st.status = False, "Confirmation reader unavailable"
                return
        st.hit = hit
        if hit:
            st.status = tag + ("Confirmed · alert sent" if send else
                               "Confirming reveal…" if st.alert_guard.hits < 2 else "Reveal visible · duplicate blocked")
        else:
            st.status = tag + "Scanning · text confirmation on"
        if send and self.running and st.watch and self.states.get(st.hwnd) is st:
            self.log(f"{st.label}: reveal confirmed on consecutive scans. Sending alert…")
            if s.get("sound_alert", True):
                play_sound()
            counts = self.record_hatch(st.label)
            threading.Thread(target=self._send_alert, args=(st, frame, dict(s), False, counts), daemon=True).start()

    # ------------------------------------------------------- notifications
    def _send_alert(self, st, frame, s, is_test, counts=None):
        uid, label = s["discord_id"], st.label
        pre = "🧪 TEST — " if is_test else ""
        if st.hwnd:
            try:
                snap = snap_window(st.hwnd, allow_peek=s["minimized"], full=s["fullsize"])
                if snap is not None and snap.size and snap.mean() >= 2:
                    frame = snap
            except Exception:
                pass
        try:
            post_discord({"username": "Secret Hatch Notifer+",
                          "content": f"<@{uid}> {pre}🥚 **SECRET HATCH!**",
                          "embeds": [{"title": f"{pre}🔒 A secret is hatching!",
                                      "description": f"Account: **{label}**",
                                      "color": 0xFFA516, "image": {"url": "attachment://secret.jpg"},
                                      **({"footer": {"text": f"Secret #{counts['today']} today  •  #{counts['total']} all time  "
                                                             f"•  {label}: {counts['account_total']} total"}} if counts else {}),
                                      "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}]},
                         to_jpeg_bytes(frame), ping_id=uid, test=is_test)
            self.log(f"{pre}Alert + picture sent for {label}." + (" (test channel)" if is_test else ""))
        except Exception as e:
            self.fail_count += 1
            self.log(f"Alert failed for {label}: {e}")
            return
        if is_test or st.hwnd == 0 or not s.get("reveal_pic"):
            return  # one picture per secret unless the 2nd (after-reveal) picture is switched on
        time.sleep(s["reveal_delay"])  # let the reveal finish, then send what actually hatched
        try:
            f2 = snap_window(st.hwnd, allow_peek=s["minimized"], full=s["fullsize"])
            if f2 is None or f2.size == 0:
                return
            post_discord({"username": "Secret Hatch Notifer+",
                          "embeds": [{"title": "✨ Secret revealed", "description": f"Account: **{label}**",
                                      "color": 0x2F6BFF, "image": {"url": "attachment://secret.jpg"}}]},
                         to_jpeg_bytes(f2))
            self.log(f"Reveal picture sent for {label}.")
        except Exception as e:
            self.log(f"Reveal picture failed for {label}: {e}")

    def _send_warning(self, label, reason, frame=None, s=None):
        return  # Disconnect notifications remain disabled.

    def on_test_alert(self):
        if not self._valid_id():
            return
        if time.time() - self.last_test < 30:
            self.log("Test alerts are limited to one every 30 seconds.")
            return
        self.last_test = time.time()
        st = self._pick()
        if st is None and len(self.states) == 1:
            st = next(iter(self.states.values()))
        if st is None:
            self.log("Click the account you want to test first. A test alert sends a picture of that one window.")
            return
        s = dict(self.settings)

        def run():
            try:
                frame = snap_window(st.hwnd, allow_peek=True, full=s["fullsize"])
            except Exception as e:
                self.log(f"Couldn't capture {st.label}: {e}")
                return
            if frame is None or frame.size == 0:
                self.log(f"Couldn't capture {st.label}, so no test was sent.")
                return
            self.log(f"Test picture taken from {st.label} only.")
            self._send_alert(st, frame, s, True)
        threading.Thread(target=run, daemon=True).start()

    def _popup(self, title, w, h):
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.configure(fg_color=BG)
        win.geometry(f"{w}x{h}")
        win.after(100, win.focus)
        return win

    @staticmethod
    def _fit(frame, max_w, max_h):
        """A picture of a Roblox window sized to fit a box (small windows are enlarged a little, never squashed)."""
        im = Image.fromarray(frame)
        sc = min(max_w / im.width, max_h / im.height, 2)
        return ctk.CTkImage(light_image=im, dark_image=im, size=(max(1, int(im.width * sc)), max(1, int(im.height * sc))))

    def on_view(self):
        messagebox.showinfo("Previews disabled", "Live previews are disabled to reduce CPU usage. Use Bring to front to view Roblox. Detection continues while watching is enabled.")

    def on_test_read(self):
        st = self._selected()
        if not st:
            return
        self._sync()

        def run():
            try:
                frame = Grabber().capture(st.hwnd, "Auto", allow_peek=True, full=self.settings["fullsize"])
                if frame is None or frame.size == 0:
                    self.log(f"Test: couldn't capture {st.label}.")
                    return
                hit, why, text = self.analyze(frame)
                self.after(0, lambda: self._show_test(st.label, frame, hit, why, text))
            except Exception as e:
                self.log(f"Test failed: {e}")
        threading.Thread(target=run, daemon=True).start()

    def _show_test(self, label, frame, hit, why, text):
        win = self._popup(f"Detection check: {label}", 760, 600)
        cimg = self._fit(frame, 720, 405)
        ctk.CTkLabel(win, image=cimg, text="", fg_color=STAGE, corner_radius=0, width=720, height=405
                     ).pack(padx=20, pady=(20, 14))
        color = GOLD if hit else MUTED
        ctk.CTkLabel(win, text=f"   ●  Secret screen detected ({why})   " if hit else "   ●  No secret on screen   ",
                     text_color=color, fg_color=CHIP_BG.get(color, PANEL2), corner_radius=12, height=30,
                     font=F(13, True)).pack()
        ctk.CTkLabel(win, text="That is normal when no secret is showing. Nothing was sent." if not hit else
                     "This was only a check. Nothing was sent.", text_color=MUTED, font=F(12)).pack(pady=(8, 0))
        ctk.CTkLabel(win, text=f"Text the app could read: {(text or 'none')[:240]}", text_color=FAINT, font=F(12),
                     wraplength=700, justify="left").pack(padx=20, pady=(6, 16))

    def _tick(self):
        self._sync()
        self._tick_n += 1
        if self._tick_n % 4 == 0:
            self.scan_windows()
        else:
            self._refresh_table()
        if self.page == "stats" and self.state() != "iconic":
            self.refresh_stats()
        self._drain()
        self.after(2500, self._tick)

    # --------------------------------------------------------------- updates
    def check_updates(self, manual=False):
        def run():
            if not UPDATE_URL:
                if manual:
                    self.log("Update checking isn't set up in this build (UPDATE_URL is empty).")
                return
            try:
                ver, url, notes, asset = fetch_update_info()
                if ver and parse_version(ver) > parse_version(APP_VERSION):
                    self.after(0, self._show_update, ver, url, notes, asset)
                elif manual:
                    self.log(f"You're up to date (v{APP_VERSION}).")
            except Exception as e:
                if manual:
                    self.log(f"Couldn't check for updates: {str(e)[:80]}")
        threading.Thread(target=run, daemon=True).start()

    def _show_update(self, ver, url, notes, asset):
        if parse_version(ver) <= parse_version(APP_VERSION):
            return
        self.update_info = (ver, url, notes, asset)
        label = ver if ver.lower().startswith("v") else "v" + ver
        self.update_btn.configure(text=f"⬆  Update to {label}", state="normal")
        self.update_btn.grid()
        if getattr(self, "_update_logged", None) != ver:
            self._update_logged = ver
            self.log(f"NEW UPDATE {label} is available (you have v{APP_VERSION}). Click the Update button at the bottom-left."
                     + (f" {notes[:120]}" if notes else ""))

    def on_update_click(self):
        if not self.update_info or self._updating:
            return
        ver, url, notes, asset = self.update_info
        if parse_version(ver) <= parse_version(APP_VERSION):
            return
        label = ver if ver.lower().startswith("v") else "v" + ver
        installed = bool(getattr(sys, "frozen", False))
        if not (installed and asset.startswith("https://")):
            if url:  # running from source, or the release has no .exe attached: open the download page
                webbrowser.open(url)
            return
        msg = f"Install {label} now?\n\nThe app will download the update, close, replace itself and reopen."
        if notes.strip():
            msg += "\n\nWhat's new:\n" + notes.strip()[:400]
        if not messagebox.askyesno("Update Secret Hatch Notifer+", msg):
            return
        self._updating = True
        self.update_btn.configure(text="Downloading… 0%", state="disabled")
        self.log(f"Downloading {label}…")

        def fail(e):
            self._updating = False
            self.update_btn.configure(text="⚠  Update failed: click to retry", state="normal")
            self.log(f"Update failed: {str(e)[:120]}")

        def run():
            try:
                folder = os.path.join(tempfile.gettempdir(), "SecretHatchNotiferUpdate")
                os.makedirs(folder, exist_ok=True)
                newexe = os.path.join(folder, "new.exe")
                with requests.get(asset, stream=True, timeout=30, headers={"User-Agent": "SecretHatchNotifer"}) as r:
                    r.raise_for_status()
                    total, got, last = int(r.headers.get("content-length") or 0), 0, -1
                    with open(newexe, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                            got += len(chunk)
                            pct = int(got * 100 / total) if total else 0
                            if pct != last:
                                last = pct
                                self.after(0, lambda p=pct: self.update_btn.configure(text=f"Downloading… {p}%"))
                with open(newexe, "rb") as f:
                    head = f.read(2)
                if got < 5 * 1024 * 1024 or head != b"MZ" or (total and got != total):
                    raise RuntimeError("the downloaded file looks incomplete")
                self.after(0, self._quit_for_update, newexe)
            except Exception as e:
                self.after(0, fail, e)
        threading.Thread(target=run, daemon=True).start()

    def _quit_for_update(self, newexe):
        target = sys.executable
        bat_path = os.path.join(os.path.dirname(newexe), "apply_update.bat")
        bat = (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            "set TRIES=0\r\n"
            ":wait\r\n"
            "ping -n 2 127.0.0.1 >nul\r\n"
            "set /a TRIES+=1\r\n"
            f'move /y "{newexe}" "{target}" >nul 2>&1\r\n'
            "if not errorlevel 1 goto done\r\n"
            "if %TRIES% GEQ 90 goto fail\r\n"
            "goto wait\r\n"
            ":done\r\n"
            "ping -n 3 127.0.0.1 >nul\r\n"
            f'start "" "{target}"\r\n'
            '(goto) 2>nul & del "%~f0"\r\n'
            ":fail\r\n"
            f'start "" "{target}"\r\n'
        )
        with open(bat_path, "w", encoding="utf-8", newline="") as f:
            f.write(bat)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen(["cmd.exe", "/c", bat_path], creationflags=0x08000000 | 0x00000200, close_fds=True, env=env)
        self.log("Update downloaded. Restarting…")
        self.on_close()
        os._exit(0)

    def _periodic_update_check(self):
        self.check_updates(False)
        self.after(6 * 3600 * 1000, self._periodic_update_check)

    def on_close(self):
        self.stop.set()
        self._sync()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
