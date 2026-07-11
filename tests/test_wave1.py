"""Wave-1 features: idle retry (P3), session hotwords (P4), hotkey parsing (U1)."""
import queue

import numpy as np

from vc_translator.hotkeys import parse_hotkey, MOD_ALT, MOD_CONTROL
from vc_translator.pipeline import Pipeline
from vc_translator.stt import TranscriptResult


def _bare_pipeline():
    p = Pipeline.__new__(Pipeline)
    p._preprocess = False
    p._retry = []
    p._last_seg_time = 0.0
    p._base_hotwords = "rotate spike Jett"
    p._session_words = {}
    p._tr_q = queue.Queue()
    p.history = None
    return p


# ---------------- U1 hotkey parsing ----------------

def test_parse_basic():
    assert parse_hotkey("ctrl+alt+t") == (MOD_CONTROL | MOD_ALT, ord("T"))


def test_parse_function_key():
    mods, vk = parse_hotkey("ctrl+F5")
    assert mods == MOD_CONTROL and vk == 0x70 + 4


def test_parse_invalid():
    assert parse_hotkey("ctrl+alt") is None
    assert parse_hotkey("") is None
    assert parse_hotkey("ctrl+alt+escapekey") is None


# ---------------- P4 session hotwords ----------------

def test_session_words_feed_hotwords():
    p = _bare_pipeline()

    class T:
        hotwords = ""
    p.transcriber = T()
    p._learn_session_words("Chamber is holding long with Chamber ult")
    assert "chamber" in p.transcriber.hotwords
    assert p.transcriber.hotwords.startswith("rotate spike Jett")


def test_session_words_skip_base_terms():
    p = _bare_pipeline()

    class T:
        hotwords = ""
    p.transcriber = T()
    p._learn_session_words("rotate rotate rotate")   # already in base hotwords
    assert p._session_words == {}


# ---------------- P3 idle retry ----------------

class _RetryUI:
    def __init__(self):
        self.calls = []

    def line_refined(self, uid, hist_id, en, words):
        self.calls.append((uid, hist_id, en))


def test_idle_retry_upgrades_line():
    p = _bare_pipeline()
    p.ui = _RetryUI()
    p._retry = [(7, 42, np.zeros(1600, dtype=np.float32))]

    class T:
        def transcribe(self, audio, beam_size=None, word_timestamps=False, context=""):
            return TranscriptResult(text="upgraded line", avg_logprob=-0.2,
                                    low_confidence=False, words=[("upgraded", 0, 0.5)])
    p.transcriber = T()
    p._idle_retry()
    assert p.ui.calls == [(7, 42, "upgraded line")]
    assert p._retry == []                       # consumed
    requeued = p._tr_q.get_nowait()             # re-translation of the fixed text
    assert requeued[0] == 7 and requeued[1] == "upgraded line"


def test_idle_retry_skips_when_recent_speech():
    import time
    p = _bare_pipeline()
    p.ui = _RetryUI()
    p._retry = [(7, 42, np.zeros(1600, dtype=np.float32))]
    p._last_seg_time = time.monotonic()          # speech just happened
    p.transcriber = None                         # would crash if it tried
    p._idle_retry()
    assert p._retry                              # untouched


def test_idle_retry_drops_still_low_confidence():
    p = _bare_pipeline()
    p.ui = _RetryUI()
    p._retry = [(7, 42, np.zeros(1600, dtype=np.float32))]

    class T:
        def transcribe(self, audio, beam_size=None, word_timestamps=False, context=""):
            return TranscriptResult(text="still bad", avg_logprob=-2.0, low_confidence=True)
    p.transcriber = T()
    p._idle_retry()
    assert p.ui.calls == []                     # not shown, but consumed
    assert p._retry == []
