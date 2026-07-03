"""Subtitle UIs.

SubtitleOverlay: always-on-top, click-through tkinter window (bottom-center).
Two-stage display: English appears as soon as transcription finishes, the
Japanese translation is filled into the same row when it arrives.

ConsoleUI: plain stdout output for testing (phase 2-4 of the build plan).

Both expose the same thread-safe API: add_entry() / set_translation() /
run() / close(). run() blocks the main thread.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
import time

log = logging.getLogger("overlay")

_BG = "#101014"
_EN_FG = "#a8adb5"
_JA_FG = "#ffffff"
_PENDING_FG = "#5f6570"

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080


class ConsoleUI:
    def __init__(self):
        self._stop = threading.Event()

    def add_entry(self, uid: int, english: str):
        print(f"  [EN #{uid}] {english}", flush=True)

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
        """Standalone (master=None): owns a tk.Tk root; call run() to block.
        Embedded in an app (master=app root): uses a Toplevel and the app's
        mainloop; call start_polling() once after creation."""
        import tkinter as tk
        import tkinter.font as tkfont

        self._tk = tk
        self.cfg = cfg
        self._q: queue.Queue = queue.Queue()
        self._entries: dict[int, dict] = {}  # uid -> {frame, ja_label, created}

        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.withdraw()  # hidden until the first subtitle arrives
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", float(cfg.get("opacity", 0.88)))
        self.root.configure(bg=_BG)

        family = cfg.get("font_family", "Yu Gothic UI")
        available = set(tkfont.families())
        if family not in available:
            family = "Meiryo UI" if "Meiryo UI" in available else "TkDefaultFont"
        self._font_en = (family, int(cfg.get("font_size_en", 13)))
        self._font_ja = (family, int(cfg.get("font_size_ja", 17)), "bold")

        self.width = int(cfg.get("width", 900))
        self.container = tk.Frame(self.root, bg=_BG)
        self.container.pack(fill="both", expand=True, padx=14, pady=8)

        self._click_through_applied = False

    # -- thread-safe API ---------------------------------------------------

    def add_entry(self, uid: int, english: str):
        self._q.put(("add", uid, english))

    def set_translation(self, uid: int, japanese: str):
        self._q.put(("ja", uid, japanese))

    def close(self):
        self._q.put(("close", None, None))

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
                op, uid, text = self._q.get_nowait()
                if op == "close":
                    self.root.destroy()
                    return
                if op == "add":
                    self._add_row(uid, text)
                    changed = True
                elif op == "ja" and uid in self._entries:
                    entry = self._entries[uid]
                    if text:
                        entry["ja_label"].configure(text=text, fg=_JA_FG)
                    else:
                        entry["ja_label"].pack_forget()  # translation disabled
                    changed = True
        except queue.Empty:
            pass

        if self._prune() or changed:
            self._relayout()
        self.root.after(50, self._poll)

    def _add_row(self, uid: int, english: str):
        tk = self._tk
        frame = tk.Frame(self.container, bg=_BG)
        frame.pack(fill="x", anchor="w", pady=(0, 6))
        wrap = self.width - 40
        if self.cfg.get("show_english", True):
            en = tk.Label(frame, text=english, font=self._font_en, fg=_EN_FG, bg=_BG,
                          wraplength=wrap, justify="left", anchor="w")
            en.pack(fill="x", anchor="w")
        ja = tk.Label(frame, text="…", font=self._font_ja, fg=_PENDING_FG, bg=_BG,
                      wraplength=wrap, justify="left", anchor="w")
        ja.pack(fill="x", anchor="w")
        self._entries[uid] = {"frame": frame, "ja_label": ja, "created": time.monotonic()}

    def _prune(self) -> bool:
        now = time.monotonic()
        fade_after = float(self.cfg.get("fade_after_s", 14))
        max_lines = int(self.cfg.get("max_lines", 4))
        stale = [uid for uid, e in self._entries.items() if now - e["created"] > fade_after]
        overflow = sorted(self._entries, key=lambda u: self._entries[u]["created"])
        stale += overflow[:max(0, len(self._entries) - max_lines)]
        removed = False
        for uid in dict.fromkeys(stale):
            self._entries.pop(uid)["frame"].destroy()
            removed = True
        return removed

    def _relayout(self):
        if not self._entries:
            self.root.withdraw()
            return
        self.root.deiconify()
        self.root.update_idletasks()
        height = self.container.winfo_reqheight() + 16
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - self.width) // 2 + int(self.cfg.get("x_offset", 0))
        y = screen_h - int(self.cfg.get("y_offset", 140)) - height
        self.root.geometry(f"{self.width}x{height}+{x}+{y}")
        if not self._click_through_applied:
            self._apply_click_through()

    def _apply_click_through(self):
        self._click_through_applied = True
        if sys.platform != "win32" or not self.cfg.get("click_through", True):
            return
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            style = get_long(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            set_long(hwnd, GWL_EXSTYLE, style)
            log.info("overlay click-through enabled")
        except Exception as exc:
            log.warning("could not enable click-through: %s", exc)
