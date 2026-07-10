"""Uncaught-exception logging so field crashes leave a diagnosable trace."""

from __future__ import annotations

import datetime as _dt
import logging
import sys
import threading
import traceback

log = logging.getLogger("crash")


def _write(kind: str, text: str):
    try:
        from vc_translator import paths
        path = paths.data_dir() / f"crash-{_dt.datetime.now():%Y%m%d_%H%M%S}.log"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{kind}\n\n{text}")
        log.error("uncaught %s written to %s", kind, path)
    except Exception:
        log.exception("failed to write crash log")


def install():
    """Route uncaught exceptions (main + worker threads) to data/crash-*.log."""
    def _hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _write("main-thread exception", "".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = _hook

    def _thread_hook(args):
        _write(f"thread {args.thread.name} exception",
               "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    try:
        threading.excepthook = _thread_hook  # Python 3.8+
    except Exception:
        pass
