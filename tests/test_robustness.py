"""M2 runtime robustness: audio reconnect watchdog + Ollama translate recovery."""
import queue
import threading
import time

import numpy as np


def test_audio_reconnect_on_stall(monkeypatch):
    """blocks() should notify 'lost', reconnect, then 'ok' when data resumes."""
    import sys
    import types

    # stub sounddevice so AudioCapture imports without a real backend
    fake_sd = types.SimpleNamespace(
        query_devices=lambda *a: [{"name": "CABLE Output", "max_input_channels": 2,
                                   "default_samplerate": 16000, "hostapi": 0}],
        # real API: no arg -> list of hostapis; with an index -> that hostapi dict
        query_hostapis=lambda *a: ({"name": "WASAPI"} if a else [{"name": "WASAPI"}]),
        InputStream=lambda **k: types.SimpleNamespace(
            start=lambda: None, stop=lambda: None, close=lambda: None, active=True),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    from vc_translator.audio import AudioCapture

    states = []
    cap = AudioCapture("CABLE Output", target_sr=16000, on_status=states.append)
    reconnected = threading.Event()
    monkeypatch.setattr(cap, "_reconnect", lambda ev: (states.append("reconnected"), reconnected.set(), True)[-1])

    stop = threading.Event()
    got = []

    def consume():
        for b in cap.blocks(stop):
            got.append(b)

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    time.sleep(3.4)                       # let the 3s stall watchdog fire
    assert "lost" in states               # reported loss
    assert reconnected.is_set()           # attempted reconnect
    cap._q.put(np.ones(160, dtype=np.float32))  # data resumes
    time.sleep(0.4)
    stop.set(); t.join(timeout=2)
    assert "reconnected" in states        # recovery reported
    assert got                            # yielded a block after recovery


def test_translate_loop_recovers_ollama(monkeypatch):
    """A translate() ConnectionError triggers ensure_server()+retry; second attempt wins."""
    from vc_translator import pipeline

    calls = {"n": 0}

    class FakeTranslator:
        class client:
            @staticmethod
            def ensure_server():
                return True
        def warmup(self):
            pass
        def translate(self, text, context=""):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("ollama down")
            return "回復した訳"

    class FakeUI:
        def __init__(self):
            self.translations = {}
            self.llm_notes = []
        def add_entry(self, *a, **k): pass
        def set_translation(self, uid, ja): self.translations[uid] = ja
        def note_llm(self, s): self.llm_notes.append(s)

    p = pipeline.Pipeline.__new__(pipeline.Pipeline)
    p.translator = FakeTranslator()
    p.suggester = None
    p.history = None
    p.ui = FakeUI()
    p.stop_event = threading.Event()
    p.last_latency = {"stt": None, "translate": None}
    p._tr_q = queue.Queue()
    p._sug_q = queue.Queue()
    p._tr_q.put((1, "push him", None, ""))
    p._tr_q.put(None)  # sentinel to end the loop

    p._translate_loop()
    assert p.ui.translations[1] == "回復した訳"
    assert "recovering" in p.ui.llm_notes and "ok" in p.ui.llm_notes
