"""Global hotkeys via Win32 RegisterHotKey (U1).

Works while the game is focused, so the player never has to Alt-Tab:
  - toggle the pipeline (start/stop)
  - star the most recent line ("save that phrase!")
  - show/hide the subtitle overlay

Hotkey strings look like "ctrl+alt+t". Registration happens on a dedicated
thread that owns the message loop (a Win32 requirement).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading

log = logging.getLogger("hotkeys")

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
         "shift": MOD_SHIFT, "win": MOD_WIN}


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """'ctrl+alt+t' -> (modifiers, virtual_key). None if unparseable."""
    if not spec:
        return None
    mods = 0
    vk = None
    for part in str(spec).lower().replace(" ", "").split("+"):
        if part in _MODS:
            mods |= _MODS[part]
        elif len(part) == 1 and (part.isalnum()):
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit():  # F1..F24
            n = int(part[1:])
            if 1 <= n <= 24:
                vk = 0x70 + n - 1
        else:
            return None
    if vk is None:
        return None
    return mods, vk


class HotkeyManager:
    """Registers hotkeys on its own thread; fires callbacks on that thread."""

    def __init__(self, bindings: dict[str, str], callbacks: dict[str, "callable"]):
        """bindings: name -> 'ctrl+alt+t'; callbacks: name -> fn()."""
        self._bindings = bindings
        self._callbacks = callbacks
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self.registered: list[str] = []

    def start(self):
        if sys.platform != "win32":
            return
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread_id is not None:
            try:  # WM_QUIT unblocks GetMessage so the thread exits cleanly
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
            except Exception:
                pass

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        ids: dict[int, str] = {}
        for i, (name, spec) in enumerate(self._bindings.items(), start=1):
            parsed = parse_hotkey(spec)
            if parsed is None:
                log.warning("hotkey %s: cannot parse %r", name, spec)
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                ids[i] = name
                self.registered.append(name)
                log.info("hotkey registered: %s = %s", name, spec)
            else:
                log.warning("hotkey %s (%s) already in use by another app", name, spec)

        if not ids:
            return
        import ctypes.wintypes as wintypes
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                name = ids[msg.wParam]
                cb = self._callbacks.get(name)
                if cb:
                    try:
                        cb()
                    except Exception:
                        log.exception("hotkey callback %s failed", name)
        for hot_id in ids:
            try:
                user32.UnregisterHotKey(None, hot_id)
            except Exception:
                pass
