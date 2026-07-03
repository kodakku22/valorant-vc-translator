"""Wires capture -> VAD -> STT -> translation (-> suggestions) as a threaded pipeline.

Threads:
  capture:   audio source -> SpeechSegmenter -> seg_q
  stt:       seg_q -> Transcriber -> UI (English, immediately) -> tr_q
  translate: tr_q -> Translator -> UI (Japanese, same row) -> sug_q
  suggest:   sug_q -> Suggester -> UI (reply suggestions) + history  [optional]

Heavy components (Whisper model, Ollama clients) can be built once with
build_components() and injected, so a GUI can stop/start pipelines without
reloading models. Each stage logs its latency for tuning.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time

log = logging.getLogger("pipeline")


def build_components(cfg: dict, glossary: dict, no_translate: bool = False,
                     with_suggester: bool = True):
    """Build (transcriber, translator, suggester) once; reuse across pipeline runs."""
    from vc_translator.stt import Transcriber

    stt = cfg.get("stt", {})
    transcriber = Transcriber(
        model=stt.get("model", "large-v3"),
        device=stt.get("device", "cuda"),
        compute_type=stt.get("compute_type", "int8_float16"),
        beam_size=int(stt.get("beam_size", 2)),
        no_speech_threshold=float(stt.get("no_speech_threshold", 0.6)),
        hotwords=glossary.get("hotwords", ""),
    )

    translator = None
    suggester = None
    if not no_translate:
        from vc_translator.translate import Translator
        from vc_translator.suggest import Suggester

        tr = cfg.get("translate", {})
        translator = Translator(
            model=tr.get("model", "gemma4:latest"),
            host=tr.get("host", "http://127.0.0.1:11434"),
            think=tr.get("think", False),
            keep_alive=tr.get("keep_alive", -1),
            temperature=float(tr.get("temperature", 0.2)),
            timeout_s=float(tr.get("timeout_s", 30)),
            terms=glossary.get("terms", {}),
        )
        if with_suggester:
            suggester = Suggester(translator.client)
    return transcriber, translator, suggester


class Pipeline:
    def __init__(self, cfg: dict, glossary: dict, ui, source,
                 transcriber=None, translator=None, suggester=None,
                 history=None, no_translate: bool = False):
        self.cfg = cfg
        self.ui = ui
        self.source = source
        self.history = history

        if transcriber is None:
            transcriber, translator, suggester = build_components(
                cfg, glossary, no_translate=no_translate)
        self.transcriber = transcriber
        self.translator = translator
        self.suggest_live = bool(cfg.get("suggest", {}).get("live", True))
        self.suggester = suggester if self.suggest_live else None

        self.stop_event = threading.Event()
        self.done_event = threading.Event()  # set when a file source is fully processed
        self.uid_to_hist: dict[int, int | None] = {}  # live uid -> utterances.id
        self.last_latency = {"stt": None, "translate": None}
        self._seg_q: queue.Queue = queue.Queue(maxsize=8)
        self._tr_q: queue.Queue = queue.Queue(maxsize=32)
        self._sug_q: queue.Queue = queue.Queue(maxsize=8)
        self._uid = itertools.count(1)
        self._capture_done = threading.Event()
        self._threads: list[threading.Thread] = []

        from vc_translator.vad import SpeechSegmenter

        vad = cfg.get("vad", {})
        self.segmenter = SpeechSegmenter(
            threshold=float(vad.get("threshold", 0.5)),
            min_silence_ms=int(vad.get("min_silence_ms", 400)),
            speech_pad_ms=int(vad.get("speech_pad_ms", 120)),
            min_speech_ms=int(vad.get("min_speech_ms", 250)),
            max_segment_s=float(vad.get("max_segment_s", 6.0)),
        )

    def start(self):
        # Open the audio stream on the main thread: PortAudio's WASAPI backend
        # needs a COM-initialized thread and fails from a plain worker thread.
        self.source.start()
        if self.history is not None:
            self.history.start_session(self.cfg.get("profile", "?"))
        targets = [("capture", self._capture_loop),
                   ("stt", self._stt_loop),
                   ("translate", self._translate_loop),
                   ("suggest", self._suggest_loop)]
        for name, target in targets:
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self.stop_event.set()
        self.source.stop()

    # -- threads -------------------------------------------------------------

    def _capture_loop(self):
        try:
            for block in self.source.blocks(self.stop_event):
                for seg in self.segmenter.feed(block):
                    self._enqueue_segment(seg)
            for seg in self.segmenter.flush():
                self._enqueue_segment(seg)
        except Exception:
            log.exception("capture thread crashed")
            self.stop_event.set()
        finally:
            self._capture_done.set()

    def _enqueue_segment(self, seg):
        try:
            self._seg_q.put_nowait(seg)
        except queue.Full:
            # STT is behind; drop the oldest segment so subtitles stay near-live.
            try:
                self._seg_q.get_nowait()
            except queue.Empty:
                pass
            log.warning("stt backlog full -- dropped oldest segment")
            self._seg_q.put_nowait(seg)

    def _stt_loop(self):
        try:
            self.transcriber.warmup()
        except Exception:
            log.exception("whisper failed to load")
            self.stop_event.set()
            self.done_event.set()
            return
        log.info("=== ready: listening for speech ===")

        while not self.stop_event.is_set():
            try:
                seg = self._seg_q.get(timeout=0.2)
            except queue.Empty:
                if self._capture_done.is_set():
                    break
                continue
            t0 = time.perf_counter()
            try:
                text = self.transcriber.transcribe(seg)
            except Exception:
                log.exception("transcription failed")
                continue
            elapsed = time.perf_counter() - t0
            log.info("stt %.2fs (audio %.1fs): %s", elapsed, len(seg) / 16000, text or "(empty)")
            if not text:
                continue
            self.last_latency["stt"] = elapsed
            uid = next(self._uid)
            hist_id = None
            if self.history is not None:
                hist_id = self.history.add_utterance(text, seg, len(seg) / 16000)
            self.uid_to_hist[uid] = hist_id
            self.ui.add_entry(uid, text)
            self._tr_q.put((uid, text, hist_id))

        # Let the translator drain, then signal completion (file mode).
        self._tr_q.put(None)

    def _translate_loop(self):
        if self.translator is not None:
            try:
                self.translator.warmup()
            except Exception as exc:
                log.error("%s", exc)
                log.error("翻訳なしで続行します(英語字幕のみ)")
                self.translator = None
                self.suggester = None

        while not self.stop_event.is_set():
            try:
                item = self._tr_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            uid, text, hist_id = item
            if self.translator is None:
                self.ui.set_translation(uid, "")
                continue
            t0 = time.perf_counter()
            try:
                ja = self.translator.translate(text)
            except Exception as exc:
                log.warning("translation failed: %s", exc)
                ja = "(翻訳失敗)"
            self.last_latency["translate"] = time.perf_counter() - t0
            log.info("translate %.2fs: %s", self.last_latency["translate"], ja)
            self.ui.set_translation(uid, ja)
            if self.history is not None:
                self.history.set_translation(hist_id, ja)
            if self.suggester is not None:
                self._enqueue_suggestion((uid, text, hist_id))

        self._sug_q.put(None)

    def _enqueue_suggestion(self, item):
        try:
            self._sug_q.put_nowait(item)
        except queue.Full:
            try:
                self._sug_q.get_nowait()  # drop oldest; suggestions are best-effort
            except queue.Empty:
                pass
            self._sug_q.put_nowait(item)

    def _suggest_loop(self):
        while not self.stop_event.is_set():
            try:
                item = self._sug_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            uid, text, hist_id = item
            if self.suggester is None:
                continue
            t0 = time.perf_counter()
            try:
                pairs = self.suggester.suggest_replies(text)
            except Exception as exc:
                log.warning("suggestion failed: %s", exc)
                continue
            log.info("suggest %.2fs: %d replies", time.perf_counter() - t0, len(pairs))
            if not pairs:
                continue
            notify = getattr(self.ui, "set_suggestions", None)
            if notify is not None:
                notify(uid, pairs)
            if self.history is not None:
                self.history.set_suggestions(hist_id, pairs)

        self.done_event.set()
