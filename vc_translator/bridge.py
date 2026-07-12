"""JS <-> Python bridge for the pywebview app.

Every public method on Api is callable from the frontend as
`pywebview.api.<name>(...)` and returns JSON-serializable data. Python pushes
events to JS via window.evaluate_js -> app.onEvent(type, data).
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import time

from vc_translator import paths

log = logging.getLogger("bridge")

# ---------------------------------------------------------------- settings

# GUI schema for config.yaml. Descriptions mirror the yaml comments (the design
# spec says the yaml comments are the source of truth for the help texts).
SETTINGS_SCHEMA = [
    {"section": "音声デバイス", "items": [
        {"path": "audio.device_name", "label": "VC 入力デバイス",
         "desc": "VB-Cable の録音側デバイス(部分一致)", "type": "text"},
        {"path": "audio.mic_name", "label": "練習用マイク",
         "desc": "シャドーイングで使う自分のマイク(部分一致)", "type": "text"},
    ]},
    {"section": "発話検出(VAD)", "items": [
        {"path": "vad.threshold", "label": "発話判定のしきい値",
         "desc": "誤検出が多ければ上げる、取りこぼしが多ければ下げる",
         "type": "slider", "min": 0.1, "max": 0.9, "step": 0.05, "unit": "", "fmt": 2},
        {"path": "vad.min_silence_ms", "label": "発話を確定する無音の長さ",
         "desc": "短い=速いが細切れ / 長い=まとまるが遅い",
         "type": "slider", "min": 200, "max": 800, "step": 50, "unit": "ms"},
        {"path": "vad.min_speech_ms", "label": "最短発話長",
         "desc": "これより短い区間は破棄(ノイズ対策)",
         "type": "slider", "min": 100, "max": 600, "step": 50, "unit": "ms"},
        {"path": "vad.max_segment_s", "label": "強制区切りの上限",
         "desc": "字幕が出ないままになるのを防ぐ",
         "type": "slider", "min": 3, "max": 10, "step": 0.5, "unit": "s", "fmt": 1},
    ]},
    {"section": "音声認識(STT)", "items": [
        {"path": "stt.model", "label": "Whisper モデル",
         "desc": "初回選択時に自動ダウンロードされる",
         "type": "select", "options": ["large-v3", "large-v3-turbo", "medium", "base"]},
        {"path": "stt.device", "label": "実行デバイス",
         "desc": "cuda 失敗時は自動で cpu にフォールバック",
         "type": "select", "options": ["cuda", "cpu"]},
        {"path": "stt.compute_type", "label": "量子化",
         "desc": "int8_float16 が品質と VRAM のバランス定番",
         "type": "select", "options": ["int8_float16", "float16", "int8"]},
        {"path": "stt.beam_size", "label": "ビームサイズ",
         "desc": "大きい=精度寄り / 小さい=速度寄り",
         "type": "slider", "min": 1, "max": 5, "step": 1, "unit": ""},
    ]},
    {"section": "翻訳(Ollama)", "items": [
        {"path": "translate.model", "label": "翻訳モデル",
         "desc": "ollama list で見えるモデル名", "type": "text"},
        {"path": "translate.temperature", "label": "温度",
         "desc": "低い=訳が安定 / 高い=表現が多様",
         "type": "slider", "min": 0.0, "max": 1.0, "step": 0.05, "unit": "", "fmt": 2},
        {"path": "translate.style", "label": "訳の口調",
         "desc": "casual=常体 / polite=です・ます / gamer=FPS口語(casualと近い訳になる場合あり)。次の開始から反映",
         "type": "select", "options": ["casual", "polite", "gamer"]},
        {"path": "playback.pitch_preserve", "label": "スロー再生でピッチ維持",
         "desc": "オフだと 0.5×/0.75× で声が低くなる", "type": "toggle"},
        {"path": "suggest.live", "label": "ライブ返答サジェスト",
         "desc": "試合中に「こう返せる」英語例を表示", "type": "toggle"},
    ]},
    {"section": "オーバーレイ", "items": [
        {"path": "overlay.width", "label": "字幕の横幅",
         "desc": "ゲーム上の字幕ウィンドウ幅",
         "type": "slider", "min": 600, "max": 1400, "step": 20, "unit": "px"},
        {"path": "overlay.y_offset", "label": "画面下端からの高さ",
         "desc": "大きくすると字幕が上に上がる",
         "type": "slider", "min": 60, "max": 400, "step": 10, "unit": "px"},
        {"path": "overlay.max_lines", "label": "最大行数",
         "desc": "同時に表示する字幕の行数",
         "type": "slider", "min": 2, "max": 6, "step": 1, "unit": ""},
        {"path": "overlay.fade_after_s", "label": "字幕が消えるまでの秒数",
         "desc": "この秒数を過ぎた行は自動で消える",
         "type": "slider", "min": 4, "max": 30, "step": 1, "unit": "s"},
        {"path": "overlay.opacity", "label": "不透明度",
         "desc": "字幕ウィンドウ全体の透け具合",
         "type": "slider", "min": 0.5, "max": 1.0, "step": 0.02, "unit": "", "fmt": 2},
        {"path": "overlay.show_english", "label": "英語原文も表示",
         "desc": "オフにすると日本語のみの1段表示", "type": "toggle"},
        {"path": "overlay.click_through", "label": "クリック透過",
         "desc": "字幕がゲーム操作を邪魔しないようにする", "type": "toggle"},
        {"path": "overlay._adjust", "label": "字幕の位置を調整",
         "desc": "オーバーレイをドラッグして位置を決める(ダブルクリックで確定)",
         "type": "button", "action": "adjust_overlay", "button": "🖱 調整を開始"},
    ]},
    {"section": "ホットキー", "items": [
        {"path": "hotkeys.enabled", "label": "グローバルホットキー",
         "desc": "ゲーム中でも効くショートカット(変更は次回起動時に反映)", "type": "toggle"},
        {"path": "hotkeys.toggle", "label": "翻訳の開始/停止",
         "desc": "例: ctrl+alt+t", "type": "text"},
        {"path": "hotkeys.star", "label": "直前の発言を★保存",
         "desc": "例: ctrl+alt+s — 「今のフレーズ覚えたい!」を1キーで", "type": "text"},
        {"path": "hotkeys.overlay", "label": "字幕の表示/非表示",
         "desc": "例: ctrl+alt+o", "type": "text"},
    ]},
    {"section": "履歴・保存", "items": [
        {"path": "history.enabled", "label": "履歴を保存",
         "desc": "認識・翻訳結果を data/history.db に自動保存(学習用)", "type": "toggle"},
        {"path": "history.save_audio", "label": "音声クリップも保存",
         "desc": "リスニング練習用。1試合あたり 20MB 程度", "type": "toggle"},
    ]},
]

_MASK_RE = re.compile(r"[a-zA-Z']+")


def _mask_sentence(en: str) -> str:
    """Mask the 2-3 longest words (the listening targets) with ●●●●."""
    words = sorted({m.group(0) for m in _MASK_RE.finditer(en)}, key=len, reverse=True)
    targets = [w for w in words if len(w) >= 4][:3] or words[:1]
    out = en
    for w in targets:
        out = re.sub(rf"\b{re.escape(w)}\b", "●" * min(6, max(3, len(w) // 2)), out)
    return out


def _norm_words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z']+", text.lower()) if w]


class MicRecorder:
    """On-demand mic recording, resampled to 16 kHz mono for Whisper."""

    def __init__(self, device_index: int):
        import numpy as np
        import sounddevice as sd

        self._np = np
        dev = sd.query_devices(device_index)
        self.sr = int(dev["default_samplerate"])
        self.channels = min(2, max(1, int(dev["max_input_channels"])))
        self._chunks: list = []
        self._stream = sd.InputStream(
            device=device_index, samplerate=self.sr, channels=self.channels,
            dtype="float32", callback=self._callback)

    def _callback(self, indata, frames, time_info, status):
        mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
        self._chunks.append(mono.copy())

    def start(self):
        import ctypes
        import sys
        if sys.platform == "win32":
            try:
                ctypes.windll.ole32.CoInitialize(None)
            except Exception:
                pass
        self._stream.start()

    def stop(self):
        self._stream.stop()
        self._stream.close()
        np = self._np
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(self._chunks)
        if self.sr != 16000:
            import soxr
            audio = soxr.resample(audio, self.sr, 16000).astype(np.float32)
        return audio


class Api:
    def __init__(self):
        from vc_translator.config import load_config, load_glossary
        from vc_translator.history import HistoryStore
        from vc_translator.playback import ClipPlayer

        self._load_config = load_config
        self._load_glossary = load_glossary
        self._window = None  # set by webapp after create_window

        base_cfg = load_config(paths.config_path())
        self._profile = base_cfg.get("profile", "learning")
        hist_cfg = base_cfg.get("history", {})
        self._history = HistoryStore(paths.data_dir(),
                                    save_audio=hist_cfg.get("save_audio", True))
        self._player = ClipPlayer(
            on_progress=lambda r: self._push("play_progress", {"ratio": r}),
            on_done=lambda: self._push("play_done", {}))

        self._transcriber = None
        self._translator = None
        self._suggester = None
        self._components_key = None
        self._model_lock = threading.Lock()  # serialize Whisper loads (start vs shadow)
        self._rescore_lock = threading.Lock()
        self._rescoring: set[int] = set()     # session ids being rescored (A3)
        self._pipeline = None
        self._overlay = None
        self._recorder = None
        self._shadow_utt = None
        self._busy = False
        self._live_started_at = None
        self._hotkeys = None

    # ---------------------------------------------------------- plumbing

    def attach(self, window):
        self._window = window
        self._start_hotkeys()

    def _start_hotkeys(self):
        cfg = self._load_config(paths.config_path(), self._profile)
        hk = cfg.get("hotkeys", {}) or {}
        if not hk.get("enabled", True):
            return
        from vc_translator.hotkeys import HotkeyManager
        self._hotkeys = HotkeyManager(
            bindings={"toggle": hk.get("toggle", "ctrl+alt+t"),
                      "star": hk.get("star", "ctrl+alt+s"),
                      "overlay": hk.get("overlay", "ctrl+alt+o")},
            callbacks={"toggle": self._hk_toggle,
                       "star": self._hk_star_last,
                       "overlay": self._hk_overlay})
        self._hotkeys.start()

    def _hk_toggle(self):
        if not self._consent_path().exists():
            return  # first-run consent not accepted yet -- no recording
        if self._pipeline is not None:
            self.stop_pipeline()
        elif not self._busy:
            self.start_pipeline(self._profile)

    def _hk_star_last(self):
        pipeline = self._pipeline
        if pipeline is None or not pipeline.uid_to_hist:
            return
        uid, hist_id = next(reversed(pipeline.uid_to_hist.items()))
        if hist_id is None:
            return
        starred = self._history.toggle_star(hist_id)
        if self._overlay is not None:
            try:
                self._overlay.flash("★ 保存しました" if starred else "☆ 保存を解除")
            except Exception:
                pass
        self._push("line_starred", {"uid": uid, "utt_id": hist_id, "starred": starred,
                                    "due_count": self._history.due_count()})

    def _hk_overlay(self):
        if self._overlay is not None:
            try:
                self._overlay.toggle_visible()
            except Exception:
                pass

    def _push(self, kind: str, data: dict):
        if self._window is None:
            return
        try:
            payload = json.dumps({"type": kind, "data": data}, ensure_ascii=False)
            self._window.evaluate_js(f"app.onEvent({payload})")
        except Exception:
            pass  # window closing

    # ---------------------------------------------------------- boot / status

    def get_boot(self):
        cfg = self._load_config(paths.config_path(), self._profile)
        return {
            "profile": self._profile,
            "status": self.get_status(),
            "due_count": self._history.due_count(),
            "suggest_live": bool(cfg.get("suggest", {}).get("live", True)),
            "pipeline_labels": self._pipeline_labels(cfg),
            "consented": self._consent_path().exists(),
        }

    # ---------------------------------------------------------- setup / consent

    def _consent_path(self):
        return paths.data_dir() / "consent.json"

    def accept_consent(self):
        import json as _json
        self._consent_path().write_text(_json.dumps({"accepted": True}), encoding="utf-8")
        return {"ok": True}

    def check_setup(self):
        """Report which prerequisites are satisfied, for the setup screen."""
        import requests

        cfg = self._load_config(paths.config_path(), self._profile)
        result = {"vbcable": False, "mic": False, "ollama": False,
                  "model": False, "whisper": False, "details": {}}
        # audio devices
        try:
            import sounddevice as sd
            hostapis = sd.query_hostapis()
            for dev in sd.query_devices():
                if dev["max_input_channels"] <= 0:
                    continue
                name = dev["name"].lower()
                if "cable output" in name:
                    result["vbcable"] = True
                elif "WASAPI" in hostapis[dev["hostapi"]]["name"]:
                    result["mic"] = True
        except Exception as exc:
            result["details"]["audio"] = str(exc)
        # ollama + model
        tr = cfg.get("translate", {})
        host = tr.get("host", "http://127.0.0.1:11434").replace("//localhost", "//127.0.0.1")
        want_model = tr.get("model", "gemma4:latest")
        try:
            requests.get(host.rstrip("/") + "/api/version", timeout=3)
            result["ollama"] = True
            tags = requests.get(host.rstrip("/") + "/api/tags", timeout=5).json()
            names = [m.get("name", "") for m in tags.get("models", [])]
            base = want_model.split(":")[0]
            result["model"] = any(n == want_model or n.split(":")[0] == base for n in names)
            result["details"]["models"] = names
        except Exception as exc:
            result["details"]["ollama"] = str(exc)
        # whisper cache
        try:
            from pathlib import Path
            model = cfg.get("stt", {}).get("model", "large-v3")
            cache = Path.home() / ".cache" / "huggingface" / "hub"
            hits = list(cache.glob(f"models--*faster-whisper-{model}*"))
            result["whisper"] = bool(hits)
        except Exception:
            pass
        result["want_model"] = want_model
        return result

    def loopback_test(self):
        """Play a tone into CABLE Input and confirm it comes back on CABLE Output,
        proving VB-Cable routing works. Runs in the background; pushes result."""
        if self._pipeline is not None:
            return {"ok": False, "error": "翻訳の稼働中はテストできません"}
        threading.Thread(target=self._loopback_worker, daemon=True).start()
        return {"ok": True}

    def _loopback_worker(self):
        import ctypes
        import sys
        import time

        import numpy as np
        try:
            import sounddevice as sd
            if sys.platform == "win32":
                try:
                    ctypes.windll.ole32.CoInitialize(None)
                except Exception:
                    pass
            hostapis = sd.query_hostapis()
            out_idx = in_idx = None
            for i, dev in enumerate(sd.query_devices()):
                api = hostapis[dev["hostapi"]]["name"]
                nm = dev["name"].lower()
                if "WASAPI" not in api:
                    continue
                if "cable input" in nm and dev["max_output_channels"] > 0 and out_idx is None:
                    out_idx = i
                if "cable output" in nm and dev["max_input_channels"] > 0 and in_idx is None:
                    in_idx = i
            if out_idx is None or in_idx is None:
                self._push("loopback_result", {"ok": False,
                           "error": "CABLE Input/Output が見つかりません(VB-Cable 未導入?)"})
                return
            sr = 48000
            tone = 0.3 * np.sin(2 * np.pi * 440 * np.arange(sr) / sr).astype(np.float32)
            rec = sd.rec(sr, samplerate=sr, channels=1, dtype="float32", device=in_idx)
            sd.play(tone, samplerate=sr, device=out_idx)
            sd.wait()
            time.sleep(0.1)
            peak = float(np.abs(rec).max())
            self._push("loopback_result", {"ok": peak > 0.02, "peak": round(peak, 4)})
        except Exception as exc:
            self._push("loopback_result", {"ok": False, "error": str(exc)})

    def _pipeline_labels(self, cfg):
        return {
            "input": (cfg.get("audio", {}).get("device_name", "?")).upper(),
            "mic": (cfg.get("audio", {}).get("mic_name", "-")).upper(),
            "stt": f"{cfg.get('stt', {}).get('model', '?')}".upper()
                   + (" · GPU" if cfg.get("stt", {}).get("device") == "cuda" else " · CPU"),
            "llm": (cfg.get("translate", {}).get("model", "?")).split(":")[0].upper(),
        }

    def get_status(self):
        state = "idle"
        if self._busy:
            state = "loading"
        elif self._pipeline is not None:
            state = "live"
        lat = None
        if self._pipeline is not None:
            stt = self._pipeline.last_latency.get("stt")
            tr = self._pipeline.last_latency.get("translate")
            if stt is not None and tr is not None:
                lat = round(stt + tr, 1)
        return {"state": state, "profile": self._profile,
                "started_at": self._live_started_at, "latency": lat}

    # ---------------------------------------------------------- pipeline

    def start_pipeline(self, profile: str):
        if self._pipeline is not None or self._busy:
            return {"ok": False, "error": "already running"}
        self._busy = True
        self._profile = profile
        threading.Thread(target=self._start_worker, args=(profile,), daemon=True).start()
        return {"ok": True}

    def _start_worker(self, profile: str):
        try:
            cfg = self._load_config(paths.config_path(), profile)
            glossary = self._load_glossary(paths.glossary_path())
            self._ensure_components(cfg, glossary)

            from vc_translator.audio import AudioCapture
            from vc_translator.pipeline import Pipeline

            audio_cfg = cfg.get("audio", {})
            source = AudioCapture(audio_cfg.get("device_name", "CABLE Output"),
                                  target_sr=int(audio_cfg.get("target_samplerate", 16000)),
                                  block_ms=int(audio_cfg.get("block_ms", 32)),
                                  on_status=lambda st: self._push("health", {"input": st}))
            # Honor the history settings toggles (re-read each run so the
            # settings screen actually takes effect).
            hist_cfg = cfg.get("history", {})
            self._history.save_audio = bool(hist_cfg.get("save_audio", True))
            history = self._history if hist_cfg.get("enabled", True) else None
            self._start_overlay(cfg)
            self._pipeline = Pipeline(
                cfg, glossary, _PipelineUI(self), source,
                transcriber=self._transcriber, translator=self._translator,
                suggester=self._suggester, history=history)
            self._pipeline.start()
            self._live_started_at = time.time()
            self._push("status", self.get_status())
            self._push("labels", self._pipeline_labels(cfg))
        except Exception as exc:
            log.exception("pipeline start failed")
            self._stop_overlay()
            self._pipeline = None
            self._push("error", {"message": str(exc)})
            self._push("status", {**self.get_status(), "state": "idle"})
        finally:
            self._busy = False
            self._push("status", self.get_status())

    def _ensure_components(self, cfg, glossary):
        from vc_translator.pipeline import build_components

        with self._model_lock:  # never load Whisper twice concurrently (VRAM/OOM)
            key = (cfg["stt"].get("model"), cfg["translate"].get("model"),
                   cfg["translate"].get("style", "casual"))
            if self._components_key == key and self._transcriber is not None:
                return
            self._push("loading", {"msg": f"LOADING {key[0].upper()} · "
                                          f"{cfg['stt'].get('device', 'cuda').upper()}…"})
            transcriber, translator, suggester = build_components(cfg, glossary)
            transcriber.warmup()
            self._push("loading", {"msg": f"LOADING {key[1].split(':')[0].upper()}…"})
            if translator is not None:
                translator.warmup()
            self._transcriber, self._translator, self._suggester = (
                transcriber, translator, suggester)
            self._components_key = key

    def stop_pipeline(self):
        pipeline = self._pipeline
        if pipeline is not None:
            pipeline.stop()
            # Keep self._pipeline set during the drain so the last utterance's
            # add_entry can still resolve its uid -> utt_id link for the UI.
            pipeline.join(timeout=4.0)  # let the last utterance finish saving
        self._pipeline = None
        self._stop_overlay()
        self._history.end_session()  # only after threads drained -> no lost line
        self._live_started_at = None
        self._push("status", self.get_status())
        return {"ok": True, "due_count": self._history.due_count()}

    def _start_overlay(self, cfg):
        from vc_translator.overlay import SubtitleOverlay

        ready = threading.Event()

        def run():
            try:
                self._overlay = SubtitleOverlay(cfg.get("overlay", {}))
                ready.set()
                self._overlay.run()  # blocks this thread until closed
            except Exception:
                log.exception("overlay thread crashed")
                ready.set()

        threading.Thread(target=run, name="overlay", daemon=True).start()
        ready.wait(timeout=10)

    def _stop_overlay(self):
        if self._overlay is not None:
            try:
                self._overlay.close()
            except Exception:
                pass
            self._overlay = None

    # ---------------------------------------------------------- U2 adjust

    def adjust_overlay(self):
        """Enter drag-to-position mode; spawns a preview overlay when idle."""
        self._overlay_temp = False
        if self._overlay is None:
            cfg = self._load_config(paths.config_path(), self._profile)
            self._start_overlay(cfg)
            if self._overlay is None:
                return {"ok": False, "error": "オーバーレイを起動できませんでした"}
            self._overlay_temp = True
            self._overlay.add_entry(1, "overlay position preview")
            self._overlay.set_translation(1, "この位置に字幕が表示されます")
        try:
            self._overlay.start_adjust(self._adjust_done)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def _adjust_done(self, x_off: int, y_off: int):
        self.set_setting("overlay.x_offset", int(x_off))
        self.set_setting("overlay.y_offset", int(y_off))
        self._push("adjust_done", {"x_offset": int(x_off), "y_offset": int(y_off)})
        if getattr(self, "_overlay_temp", False):
            self._overlay_temp = False
            self._stop_overlay()

    # ---------------------------------------------------------- live tab

    def toggle_star(self, utt_id: int):
        starred = self._history.toggle_star(int(utt_id))
        return {"starred": starred, "due_count": self._history.due_count()}

    def ja_to_en(self, text: str):
        try:
            pairs = self._get_suggester().ja_to_callout(text)
            return {"ok": True, "pairs": pairs}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def save_suggestion(self, utt_id, en: str, ja: str):
        ok = self._history.add_card(en, ja, utt_id=utt_id)
        return {"ok": ok, "due_count": self._history.due_count()}

    def _get_suggester(self):
        if self._suggester is not None:
            return self._suggester
        from vc_translator.suggest import Suggester
        from vc_translator.translate import OllamaChat

        cfg = self._load_config(paths.config_path(), self._profile)
        tr = cfg.get("translate", {})
        client = OllamaChat(tr.get("model", "gemma4:latest"),
                            host=tr.get("host", "http://127.0.0.1:11434"),
                            think=tr.get("think", False),
                            keep_alive=tr.get("keep_alive", -1),
                            temperature=float(tr.get("temperature", 0.2)),
                            timeout_s=float(tr.get("timeout_s", 30)))
        client.ensure_server()
        self._suggester = Suggester(client)
        return self._suggester

    # ---------------------------------------------------------- library / review

    def get_library(self):
        sessions = self._history.list_sessions()
        days: dict[str, dict] = {}
        for s in sessions:
            day = s["started_at"][:10]
            group = days.setdefault(day, {"date": day, "sessions": [],
                                          "lines": 0, "stars": 0})
            group["sessions"].append(s)
            group["lines"] += s["lines"]
            group["stars"] += s["stars"]
        return {"days": list(days.values()), "due_count": self._history.due_count(),
                "week": self._history.week_stats()}

    def get_session(self, session_id: int):
        session_id = int(session_id)
        lines = self._history.get_session(session_id)
        meta = self._history.session_meta(session_id)
        self._maybe_rescore(session_id)  # A3: upgrade transcripts in the background
        return {
            "meta": meta,
            "lines": lines,
            "frequent": self._history.frequent_terms(session_id),
            "saved": self._history.saved_in_session(session_id),
            "due_count": self._history.due_count(),
        }

    def _pitch_preserve(self) -> bool:
        cfg = self._load_config(paths.config_path(), self._profile)
        return bool((cfg.get("playback") or {}).get("pitch_preserve", True))

    def play_line(self, utt_id: int, speed: float = 1.0, context: str = "review"):
        import os

        utt = self._history.get_utterance(int(utt_id))
        if utt is None or not utt["audio_path"]:
            return {"ok": False, "error": "音声がありません"}
        if not os.path.exists(utt["audio_path"]):
            return {"ok": False, "error": "音声ファイルが見つかりません(削除された可能性)"}
        self._player.play(utt["audio_path"], float(speed),
                          pitch_preserve=self._pitch_preserve())
        self._history.log_play(int(utt_id), float(speed), context)
        return {"ok": True}

    def play_word(self, utt_id: int, start: float, end: float, speed: float = 0.85):
        """D10: play only one word's time span from the saved clip."""
        import os

        utt = self._history.get_utterance(int(utt_id))
        if utt is None or not utt["audio_path"] or not os.path.exists(utt["audio_path"]):
            return {"ok": False, "error": "音声がありません"}
        self._player.play(utt["audio_path"], float(speed), span=(float(start), float(end)),
                          pitch_preserve=self._pitch_preserve())
        return {"ok": True}

    # ---------------------------------------------------------- A3 rescore

    def _maybe_rescore(self, session_id: int):
        """Kick off a background high-quality re-transcription of a session's
        un-refined lines (beam 5 + word timestamps). Idempotent per session."""
        cfg = self._load_config(paths.config_path(), self._profile)
        if not cfg.get("stt", {}).get("rescore", True):
            return
        with self._rescore_lock:
            if session_id in self._rescoring:
                return
            todo = self._history.unrefined_with_audio(session_id)
            if not todo:
                return
            self._rescoring.add(session_id)
        threading.Thread(target=self._rescore_worker, args=(session_id, todo),
                         daemon=True).start()

    def _rescore_worker(self, session_id: int, todo: list):
        import os

        from vc_translator.audio import read_wav_mono

        try:
            self._ensure_stt_only()  # loads Whisper if not already (guarded by lock)
        except Exception as exc:
            log.warning("rescore skipped (STT load failed): %s", exc)
            self._rescoring.discard(session_id)
            return
        done = 0
        for item in todo:
            if self._pipeline is not None:  # yield to a live match
                break
            path = item["audio_path"]
            if not path or not os.path.exists(path):
                continue
            try:
                audio, _sr = read_wav_mono(path)
                res = self._transcriber.transcribe(
                    audio, beam_size=5, word_timestamps=True)
            except Exception:
                log.exception("rescore failed for utt %s", item["id"])
                continue
            if not res.text:
                continue
            self._history.refine_utterance(item["id"], res.text, res.words)
            done += 1
            self._push("refined", {"utt_id": item["id"], "en": res.text,
                                   "words": res.words, "session_id": session_id})
        log.info("rescored %d/%d lines in session %d", done, len(todo), session_id)
        self._rescoring.discard(session_id)

    def play_pause(self):
        self._player.toggle_pause()
        return {"ok": True}

    def play_seek(self, ratio: float):
        self._player.seek(float(ratio))
        return {"ok": True}

    def play_stop(self):
        self._player.stop()
        return {"ok": True}

    def explain_line(self, utt_id: int):
        """D11: explain a saved line's meaning/slang/usage (cached in history)."""
        utt = self._history.get_utterance(int(utt_id))
        if utt is None:
            return {"ok": False, "error": "行が見つかりません"}
        if utt.get("explanation"):
            return {"ok": True, "explanation": utt["explanation"], "cached": True}
        try:
            text = self._get_suggester().explain(utt["en"], utt["ja"])
        except Exception as exc:
            return {"ok": False, "error": f"解説の生成に失敗: {exc}"}
        self._history.set_explanation(int(utt_id), text)
        return {"ok": True, "explanation": text, "cached": False}

    def mark_reviewed(self, session_id: int):
        self._history.mark_reviewed(int(session_id))
        return {"ok": True}

    def delete_session(self, session_id: int):
        self._history.delete_session(int(session_id))
        return {"ok": True, "due_count": self._history.due_count()}

    def search_history(self, query: str):
        query = (query or "").strip()
        if not query:
            return {"results": []}
        return {"results": self._history.search(query)}

    # ---------------------------------------------------------- shadowing

    def shadow_start(self, utt_id: int):
        if self._pipeline is not None or self._busy:
            return {"ok": False, "error": "翻訳の稼働中は練習できません"}
        if self._recorder is not None:
            return {"ok": False, "error": "録音中です"}
        idx = self._find_mic()
        if idx is None:
            return {"ok": False, "error": "マイクが見つかりません(設定の mic_name を確認)"}
        try:
            self._recorder = MicRecorder(idx)
            self._recorder.start()
            self._shadow_utt = int(utt_id)
            return {"ok": True}
        except Exception as exc:
            self._recorder = None
            return {"ok": False, "error": f"録音を開始できません: {exc}"}

    def shadow_stop(self):
        if self._recorder is None:
            return {"ok": False, "error": "録音していません"}
        audio = self._recorder.stop()
        self._recorder = None
        utt = self._history.get_utterance(self._shadow_utt)
        if utt is None:
            return {"ok": False, "error": "対象行が見つかりません"}
        try:
            self._ensure_stt_only()
            spoken = self._transcriber.transcribe(audio).text if len(audio) else ""
        except Exception as exc:
            return {"ok": False, "error": f"認識失敗: {exc}"}
        if not spoken:
            return {"ok": True, "spoken": "", "score": 0, "words": []}
        t_words = _norm_words(utt["en"])
        s_words = _norm_words(spoken)
        matcher = difflib.SequenceMatcher(None, t_words, s_words)
        words = []
        for op, i1, i2, _j1, _j2 in matcher.get_opcodes():
            for w in t_words[i1:i2]:
                words.append({"w": w, "ok": op == "equal"})
        return {"ok": True, "spoken": spoken,
                "score": round(matcher.ratio() * 100), "words": words}

    def _find_mic(self):
        import sounddevice as sd

        cfg = self._load_config(paths.config_path(), self._profile)
        prefer = cfg.get("audio", {}).get("mic_name", "").lower()
        hostapis = sd.query_hostapis()
        fallback = None
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] <= 0:
                continue
            if "WASAPI" not in hostapis[dev["hostapi"]]["name"]:
                continue
            if "cable" in dev["name"].lower():
                continue
            if fallback is None:
                fallback = i
            if prefer and prefer in dev["name"].lower():
                return i
        return fallback

    def _ensure_stt_only(self):
        from vc_translator.pipeline import build_components

        with self._model_lock:  # serialize against _ensure_components (start button)
            if self._transcriber is not None:
                return
            cfg = self._load_config(paths.config_path(), self._profile)
            glossary = self._load_glossary(paths.glossary_path())
            self._push("loading", {"msg": f"LOADING {cfg['stt'].get('model', '').upper()}…"})
            transcriber, translator, suggester = build_components(cfg, glossary)
            transcriber.warmup()
            self._transcriber, self._translator = transcriber, translator
            if self._suggester is None:
                self._suggester = suggester
            self._components_key = (cfg["stt"].get("model"), cfg["translate"].get("model"))
            self._push("loading", {"msg": ""})

    # ---------------------------------------------------------- review (SRS)

    def get_due_cards(self):
        cards = self._history.due_cards()
        for c in cards:
            c["masked"] = _mask_sentence(c["en"])
            ts = c.get("ts") or ""
            c["source"] = f"{ts[5:10].replace('-', '/')} {ts[11:16]} の試合" if ts else ""
        return {"cards": cards}

    def answer_card(self, card_id: int, ok: bool):
        self._history.answer_card(int(card_id), bool(ok))
        return {"due_count": self._history.due_count()}

    def play_card(self, utt_id: int, speed: float = 1.0):
        # context='card' so flashcard replays don't pollute the "聞き逃し" filter
        return self.play_line(utt_id, speed, context="card")

    # ---------------------------------------------------------- settings

    def get_settings(self):
        # Show the EFFECTIVE values for the active profile (base + profile
        # overrides merged), so what's displayed matches what actually runs.
        effective = self._load_config(paths.config_path(), self._profile)
        values = {}
        for section in SETTINGS_SCHEMA:
            for item in section["items"]:
                sec, key = item["path"].split(".")
                values[item["path"]] = (effective.get(sec) or {}).get(key)
        from vc_translator import __version__
        return {"schema": SETTINGS_SCHEMA, "values": values, "profile": self._profile,
                "version": __version__}

    def set_setting(self, path: str, value):
        # Write to base by default, so genuinely-global settings (audio device,
        # history) apply to every profile. Only write into the active profile's
        # override block when that profile ALREADY overrides this exact key --
        # otherwise a base write would be silently shadowed by the override.
        yaml_doc = self._read_yaml()
        sec, key = path.split(".")
        prof = (yaml_doc.get("profiles") or {}).get(self._profile) or {}
        if isinstance(prof.get(sec), dict) and key in prof[sec]:
            self._set_nested(yaml_doc["profiles"][self._profile], sec, key, value)
        else:
            self._set_nested(yaml_doc, sec, key, value)
        self._write_yaml(yaml_doc)
        # U2: overlay settings take effect on the live window immediately
        if sec == "overlay" and self._overlay is not None and not key.startswith("_"):
            try:
                self._overlay.update_cfg(key, value)
            except Exception:
                pass
        return {"ok": True}

    @staticmethod
    def _set_nested(doc, sec, key, value):
        if sec not in doc or doc[sec] is None:
            from ruamel.yaml.comments import CommentedMap
            doc[sec] = CommentedMap()
        doc[sec][key] = value

    def set_profile(self, profile: str):
        self._profile = profile
        yaml_doc = self._read_yaml()
        yaml_doc["profile"] = profile
        self._write_yaml(yaml_doc)
        cfg = self._load_config(paths.config_path(), profile)
        return {"ok": True, "suggest_live": bool(cfg.get("suggest", {}).get("live", True)),
                "labels": self._pipeline_labels(cfg)}

    def _read_yaml(self):
        from ruamel.yaml import YAML
        yaml = YAML()
        with open(paths.config_path(), encoding="utf-8") as f:
            return yaml.load(f)

    def _write_yaml(self, doc):
        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.preserve_quotes = True
        with open(paths.config_path(), "w", encoding="utf-8") as f:
            yaml.dump(doc, f)


class _PipelineUI:
    """Pipeline UI adapter: forwards to the overlay window + pushes to JS."""

    def __init__(self, api: Api):
        self.api = api

    def _to_overlay(self, method, *args):
        # A crash inside the (separate-thread) overlay must never break the
        # live JS push; drop the overlay reference and keep going.
        overlay = self.api._overlay
        if overlay is None:
            return
        try:
            getattr(overlay, method)(*args)
        except Exception:
            log.exception("overlay forward failed; disabling overlay")
            self.api._overlay = None

    def add_entry(self, uid, english, low_confidence=False):
        self._to_overlay("add_entry", uid, english, low_confidence)
        pipeline = self.api._pipeline
        hist_id = pipeline.uid_to_hist.get(uid) if pipeline else None
        offset = ""
        if self.api._live_started_at:
            sec = int(time.time() - self.api._live_started_at)
            offset = f"{sec // 60:02d}:{sec % 60:02d}"
        self.api._push("line", {"uid": uid, "utt_id": hist_id, "en": english,
                                "offset": offset, "low_conf": bool(low_confidence)})

    def set_translation(self, uid, japanese):
        self._to_overlay("set_translation", uid, japanese)
        self.api._push("ja", {"uid": uid, "ja": japanese})
        status = self.api.get_status()
        if status.get("latency") is not None:
            self.api._push("latency", {"latency": status["latency"]})

    def set_suggestions(self, uid, pairs):
        self.api._push("suggest", {"uid": uid, "pairs": pairs})

    def note_llm(self, state):  # "recovering" / "ok" / "down"
        self.api._push("health", {"llm": state})

    def line_refined(self, uid, hist_id, en, words):  # P3 idle retry result
        self._to_overlay("update_entry", uid, en)
        self.api._push("refined", {"uid": uid, "utt_id": hist_id, "en": en,
                                   "words": words, "session_id": None})
