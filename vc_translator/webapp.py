"""pywebview entry point for the Editorial Ink desktop app."""

from __future__ import annotations

import logging
import os

from vc_translator import paths

log = logging.getLogger("webapp")


def run_app():
    import webview

    from vc_translator.bridge import Api

    api = Api()
    index = paths.webui_dir() / "index.html"
    window = webview.create_window(
        "VC Translator",
        url=index.as_uri(),
        js_api=api,
        width=1040,
        height=720,
        min_size=(880, 560),
        background_color="#0b0b0c",
    )
    api.attach(window)

    def on_closed():
        try:
            if api._pipeline is not None:
                api.stop_pipeline()
            api._history.close()
        except Exception:
            log.exception("shutdown cleanup failed")

    window.events.closed += on_closed

    if os.environ.get("VCT_AUTOTEST"):
        import threading
        threading.Thread(target=_autotest, args=(api, window), daemon=True).start()

    webview.start(debug=bool(os.environ.get("VCT_DEBUG")))


def _autotest(api, window):
    """Packaging self-test: start pipeline, wait until live, stop, quit."""
    import time

    log.info("AUTOTEST: waiting for window")
    time.sleep(4)
    log.info("AUTOTEST: starting pipeline")
    api.start_pipeline(api._profile)
    for _ in range(120):
        if api._pipeline is not None:
            log.info("AUTOTEST: PIPELINE RUNNING OK")
            break
        time.sleep(1)
    else:
        log.error("AUTOTEST: TIMEOUT")
        window.destroy()
        return
    # "full" keeps the pipeline up long enough to feed test audio through it
    time.sleep(28 if os.environ.get("VCT_AUTOTEST") == "full" else 3)
    api.stop_pipeline()
    log.info("AUTOTEST: DONE")
    time.sleep(1)
    window.destroy()
