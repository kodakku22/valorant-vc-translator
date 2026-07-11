"""Subtitle UIs — Editorial Ink edition.

SubtitleOverlay: always-on-top, click-through tkinter window (bottom-center).
Per-row floating cards on a color-keyed transparent window: Japanese is the
primary text, the English original is a small prefix ("english" — 日本語).
The newest row gets a coral left border and a slightly larger size; older
rows dim out. Fonts are the bundled Space Grotesk / Noto Sans JP, loaded
process-privately via GDI (falls back to Yu Gothic UI).

ConsoleUI: plain stdout output for testing.

Both expose the same thread-safe API: add_entry() / set_translation() /
set_suggestions() / run() / close(). run() blocks the calling thread.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
import time

log = logging.getLogger("overlay")

# Editorial Ink tokens (tkinter approximations of the web design)
_KEY = "#010203"          # transparentcolor key -> gaps between cards vanish
_CARD_BG = "#0a0a0c"      # rgba(8,8,10,.85) approximated + window alpha
_EN_FG = "#c9c9cd"
_JA_FG = "#ffffff"
_EN_DIM = "#8a8a8f"
_JA_DIM = "#b9b9bd"
_PENDING_FG = "#6b6b6e"
_CORAL = "#ff6a52"

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
FR_PRIVATE = 0x10

_fonts_loaded = False


def _load_private_fonts():
    """Register bundled TTFs for this process only (GDI). Best-effort."""
    global _fonts_loaded
    if _fonts_loaded or sys.platform != "win32":
        return
    _fonts_loaded = True
    try:
        from vc_translator.paths import webui_dir
        fonts = webui_dir() / "fonts"
        if not fonts.is_dir():
            return
        for ttf in fonts.glob("*.ttf"):
            ctypes.windll.gdi32.AddFontResourceExW(str(ttf), FR_PRIVATE, 0)
        log.info("private fonts loaded from %s", fonts)
    except Exception:
        log.exception("private font loading failed")


class ConsoleUI:
    def __init__(self):
        self._stop = threading.Event()

    def add_entry(self, uid: int, english: str, low_confidence: bool = False):
        mark = " (?)" if low_confidence else ""
        print(f"  [EN #{uid}]{mark} {english}", flush=True)

    def set_translation(self, uid: int, japanese: str):
        if japanese:
            print(f"  [JA #{uid}] {japanese}", flush=True)

    def set_suggestions(self, uid: int, pairs: list):
        for en, ja in pairs:
            print(f"  [SG #{uid}] {en}  —  {ja}", flush=True)

    def run(self):
        try:
            while not self._stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass

    def close(self):
        self._stop.set()


class SubtitleOverlay:
    def __init__(self, cfg: dict, master=None):
        import tkinter as tk
        import tkinter.font as tkfont

        _load_private_fonts()
        self._tk = tk
        self.cfg = cfg
        self._q: queue.Queue = queue.Queue()
        self._entries: dict[int, dict] = {}  # uid -> widgets + meta

        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.withdraw()  # hidden until the first subtitle arrives
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", float(cfg.get("opacity", 0.88)))
        self.root.configure(bg=_KEY)
        try:
            self.root.attributes("-transparentcolor", _KEY)
        except Exception:
            pass  # non-Windows fallback: dark window instead of floating cards

        available = set(tkfont.families())
        self._f_en = "Space Grotesk" if "Space Grotesk" in available else "Segoe UI"
        self._f_ja = ("Noto Sans JP" if "Noto Sans JP" in available
                      else cfg.get("font_family", "Yu Gothic UI"))
        self._ja_size = int(cfg.get("font_size_ja", 15))
        self._en_size = max(10, self._ja_size - 4)

        self.width = int(cfg.get("width", 900))
        self.container = tk.Frame(self.root, bg=_KEY)
        self.container.pack(fill="both", expand=True)

        self._click_through_applied = False
        self._visible = True     # U1: hotkey can hide/show the overlay
        self._adjusting = False  # U2: drag-to-position mode
        self._adjust_cb = None
        self._drag_anchor = (0, 0)
        self._hint_row = None

    # -- thread-safe API ---------------------------------------------------

    def add_entry(self, uid: int, english: str, low_confidence: bool = False):
        self._q.put(("add", uid, english, low_confidence))

    def set_translation(self, uid: int, japanese: str):
        self._q.put(("ja", uid, japanese, None))

    def update_entry(self, uid: int, english: str):
        """P3: replace the English text after an idle-time re-recognition."""
        self._q.put(("en", uid, english, None))

    def flash(self, message: str):
        """Show a short-lived notice card (e.g. hotkey feedback)."""
        self._q.put(("flash", None, message, None))

    def toggle_visible(self):
        self._q.put(("vis", None, None, None))

    def start_adjust(self, on_done):
        """U2: make the overlay draggable; on_done(x_offset, y_offset) on confirm."""
        self._q.put(("adjust", None, on_done, None))

    def update_cfg(self, key: str, value):
        """U2: apply an overlay.* setting to the live window immediately."""
        self._q.put(("cfg", key, value, None))

    def close(self):
        self._q.put(("close", None, None, None))

    def run(self):
        self.start_polling()
        self.root.mainloop()

    def start_polling(self):
        self.root.after(50, self._poll)

    # -- main-thread internals ----------------------------------------------

    def _poll(self):
        changed = False
        try:
            while True:
                op, uid, text, extra = self._q.get_nowait()
                if op == "close":
                    self.root.destroy()
                    return
                if op == "add":
                    self._add_row(uid, text, low_confidence=bool(extra))
                    changed = True
                elif op == "flash":
                    self._show_flash(text)
                    changed = True
                elif op == "vis":
                    self._visible = not self._visible
                    changed = True
                elif op == "adjust":
                    self._begin_adjust(text)  # text carries the callback
                    changed = True
                elif op == "cfg":
                    self._apply_cfg(uid, text)  # uid=key, text=value
                    changed = True
                elif op == "en" and uid in self._entries:
                    entry = self._entries[uid]
                    entry["en_text"] = text
                    if self.cfg.get("show_english", True):
                        entry["en"].configure(text=f"“{text}”")
                    changed = True
                elif op == "ja" and uid in self._entries:
                    entry = self._entries[uid]
                    if text:
                        entry["ja"].configure(text=text)
                    else:  # translation disabled -> english becomes the main text
                        entry["ja"].configure(text=entry["en_text"], fg=_JA_FG)
                        entry["en"].pack_forget()
                    changed = True
        except queue.Empty:
            pass

        if self._prune() or changed:
            self._restyle()
            self._relayout()
        self.root.after(50, self._poll)

    def _add_row(self, uid: int, english: str, low_confidence: bool = False):
        tk = self._tk
        show_en = self.cfg.get("show_english", True)
        # Wrap within the window so long calls never get clipped off the right
        # edge (the window width is fixed at self.width).
        wrap = max(200, self.width - 60)
        # A2: mark uncertain recognitions so the reader knows not to fully trust them.
        en_prefix = "≈ " if low_confidence else ""
        row = tk.Frame(self.container, bg=_KEY)
        row.pack(anchor="center", pady=3)
        strip = tk.Frame(row, bg=_CARD_BG, width=2)
        strip.pack(side="left", fill="y")
        card = tk.Frame(row, bg=_CARD_BG)
        card.pack(side="left")
        inner = tk.Frame(card, bg=_CARD_BG)
        inner.pack(padx=16, pady=6)
        # English original above (small grey), Japanese below (bold white).
        # Stacked so each wraps independently instead of overflowing on one line.
        en = tk.Label(inner, text=f"{en_prefix}“{english}”" if show_en else "",
                      font=(self._f_en, self._en_size), fg=_EN_FG, bg=_CARD_BG,
                      wraplength=wrap, justify="center")
        if show_en:
            en.pack(anchor="center")
        ja = tk.Label(inner, text="…", font=(self._f_ja, self._ja_size, "bold"),
                      fg=_PENDING_FG, bg=_CARD_BG, wraplength=wrap, justify="center")
        ja.pack(anchor="center")
        self._entries[uid] = {"row": row, "strip": strip, "en": en, "ja": ja,
                              "en_text": english, "created": time.monotonic()}

    def _restyle(self):
        """Newest row: coral strip + bigger; older rows dim progressively."""
        uids = sorted(self._entries, key=lambda u: self._entries[u]["created"])
        n = len(uids)
        for i, uid in enumerate(uids):
            e = self._entries[uid]
            newest = i == n - 1
            age = n - 1 - i
            e["strip"].configure(bg=_CORAL if newest else _CARD_BG)
            ja_size = self._ja_size + (1 if newest else -1 if age >= 2 else 0)
            en_size = self._en_size + (1 if newest else 0)
            ja_fg = _JA_FG if age < 2 else _JA_DIM
            en_fg = _EN_FG if age < 2 else _EN_DIM
            e["ja"].configure(font=(self._f_ja, ja_size, "bold"),
                              fg=ja_fg if e["ja"]["text"] != "…" else _PENDING_FG)
            e["en"].configure(font=(self._f_en, en_size), fg=en_fg)

    def _prune(self) -> bool:
        now = time.monotonic()
        fade_after = float(self.cfg.get("fade_after_s", 14))
        max_lines = int(self.cfg.get("max_lines", 4))
        stale = [uid for uid, e in self._entries.items() if now - e["created"] > fade_after]
        overflow = sorted(self._entries, key=lambda u: self._entries[u]["created"])
        stale += overflow[:max(0, len(self._entries) - max_lines)]
        removed = False
        for uid in dict.fromkeys(stale):
            self._entries.pop(uid)["row"].destroy()
            removed = True
        return removed

    def _show_flash(self, message: str):
        """Ephemeral notice card, auto-removed after ~1.4s (hotkey feedback)."""
        tk = self._tk
        row = tk.Frame(self.container, bg=_KEY)
        row.pack(anchor="center", pady=3)
        card = tk.Frame(row, bg=_CARD_BG)
        card.pack()
        tk.Label(card, text=message, font=(self._f_ja, self._ja_size - 2, "bold"),
                 fg=_CORAL, bg=_CARD_BG, padx=16, pady=5).pack()

        def gone():
            try:
                row.destroy()
                self._restyle()
                self._relayout()
            except Exception:
                pass
        self.root.after(1400, gone)

    def _relayout(self):
        has_content = bool(self.container.winfo_children())  # rows + flash notices
        if not self._visible or not has_content:
            self.root.withdraw()
            return
        self.root.deiconify()
        self.root.update_idletasks()
        height = self.container.winfo_reqheight()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        if self._adjusting:
            # keep whatever position the user dragged to; only track height
            x, y = self.root.winfo_x(), self.root.winfo_y()
        else:
            x = (screen_w - self.width) // 2 + int(self.cfg.get("x_offset", 0))
            y = screen_h - int(self.cfg.get("y_offset", 140)) - height
        self.root.geometry(f"{self.width}x{height}+{x}+{y}")
        if not self._click_through_applied:
            self._click_through_applied = True
            self._set_click_through(bool(self.cfg.get("click_through", True)))

    def _set_click_through(self, enabled: bool):
        if sys.platform != "win32":
            return
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            style = get_long(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            if enabled:
                style |= WS_EX_TRANSPARENT
            else:
                style &= ~WS_EX_TRANSPARENT
            set_long(hwnd, GWL_EXSTYLE, style)
            log.info("overlay click-through %s", "enabled" if enabled else "disabled")
        except Exception as exc:
            log.warning("could not set click-through: %s", exc)

    # ---- U2: drag-to-position ----

    def _begin_adjust(self, on_done):
        self._adjusting = True
        self._adjust_cb = on_done
        self._set_click_through(False)
        tk = self._tk
        if self._hint_row is None:
            self._hint_row = tk.Frame(self.container, bg=_KEY)
            self._hint_row.pack(anchor="center", pady=3)
            card = tk.Frame(self._hint_row, bg=_CORAL)
            card.pack()
            tk.Label(card, text="ドラッグで移動 — ダブルクリックで確定",
                     font=(self._f_ja, self._ja_size - 3, "bold"),
                     fg=_KEY, bg=_CORAL, padx=14, pady=4).pack()
        self.root.bind("<ButtonPress-1>", self._drag_start)
        self.root.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<Double-Button-1>", self._adjust_confirm)

    def _drag_start(self, ev):
        self._drag_anchor = (ev.x_root - self.root.winfo_x(),
                             ev.y_root - self.root.winfo_y())

    def _drag_move(self, ev):
        x = ev.x_root - self._drag_anchor[0]
        y = ev.y_root - self._drag_anchor[1]
        self.root.geometry(f"+{x}+{y}")

    def _adjust_confirm(self, _ev=None):
        self.root.unbind("<ButtonPress-1>")
        self.root.unbind("<B1-Motion>")
        self.root.unbind("<Double-Button-1>")
        self._adjusting = False
        if self._hint_row is not None:
            self._hint_row.destroy()
            self._hint_row = None
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        height = self.root.winfo_height()
        x_off = x - (screen_w - self.width) // 2
        y_off = max(0, screen_h - y - height)
        self.cfg["x_offset"], self.cfg["y_offset"] = x_off, y_off
        self._set_click_through(bool(self.cfg.get("click_through", True)))
        cb, self._adjust_cb = self._adjust_cb, None
        if cb:
            try:
                cb(x_off, y_off)
            except Exception:
                log.exception("adjust callback failed")
        self._relayout()

    def _apply_cfg(self, key: str, value):
        """Live-apply an overlay setting (from the settings screen)."""
        self.cfg[key] = value
        if key == "opacity":
            try:
                self.root.attributes("-alpha", float(value))
            except Exception:
                pass
        elif key == "width":
            self.width = int(value)
        elif key == "click_through":
            self._set_click_through(bool(value))
