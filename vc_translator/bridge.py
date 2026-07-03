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
        self._pipeline = None
        self._overlay = None
        self._recorder = None
        self._shadow_utt = None
        self._busy = False
        self._live_started_at = None

    # ---------------------------------------------------------- plumbing

    def attach(self, window):
        self._window = window

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
        }

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
                                  block_ms=int(audio_cfg.get("block_ms", 32)))
            self._start_overlay(cfg)
            self._pipeline = Pipeline(
                cfg, glossary, _PipelineUI(self), source,
                transcriber=self._transcriber, translator=self._translator,
                suggester=self._suggester, history=self._history)
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

        key = (cfg["stt"].get("model"), cfg["translate"].get("model"))
        if self._components_key == key and self._transcriber is not None:
            return
        self._push("loading", {"msg": f"LOADING {key[0].upper()} · "
                                      f"{cfg['stt'].get('device', 'cuda').upper()}…"})
        transcriber, translator, suggester = build_components(cfg, glossary)
        transcriber.warmup()
        self._push("loading", {"msg": f"LOADING {key[1].split(':')[0].upper()}…"})
        if translator is not None:
            translator.warmup()
        self._transcriber, self._translator, self._suggester = transcriber, translator, suggester
        self._components_key = key

    def stop_pipeline(self):
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None
        self._stop_overlay()
        self._history.end_session()
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
        with self._history._lock:
            self._history._conn.execute(
                "INSERT INTO cards (utt_id, en, ja, created_ts, due_ts)"
                " VALUES (?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))",
                (utt_id, en, ja))
            self._history._conn.commit()
        return {"ok": True, "due_count": self._history.due_count()}

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
        return {"days": list(days.values()), "due_count": self._history.due_count()}

    def get_session(self, session_id: int):
        session_id = int(session_id)
        lines = self._history.get_session(session_id)
        meta = next((s for s in self._history.list_sessions() if s["id"] == session_id), None)
        return {
            "meta": meta,
            "lines": lines,
            "frequent": self._history.frequent_terms(session_id),
            "saved": self._history.saved_in_session(session_id),
            "due_count": self._history.due_count(),
        }

    def play_line(self, utt_id: int, speed: float = 1.0):
        utt = self._history.get_utterance(int(utt_id))
        if utt is None or not utt["audio_path"]:
            return {"ok": False, "error": "音声がありません"}
        self._player.play(utt["audio_path"], float(speed))
        self._history.log_play(int(utt_id), float(speed))
        return {"ok": True}

    def play_pause(self):
        self._player.toggle_pause()
        return {"ok": True}

    def play_seek(self, ratio: float):
        self._player.seek(float(ratio))
        return {"ok": True}

    def play_stop(self):
        self._player.stop()
        return {"ok": True}

    def mark_reviewed(self, session_id: int):
        self._history.mark_reviewed(int(session_id))
        return {"ok": True}

    def delete_session(self, session_id: int):
        self._history.delete_session(int(session_id))
        return {"ok": True}

    # ---------------------------------------------------------- shadowing

    def shadow_start(self, utt_id: int):
        if self._pipeline is not None:
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
            spoken = self._transcriber.transcribe(audio) if len(audio) else ""
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
        if self._transcriber is not None:
            return
        from vc_translator.pipeline import build_components

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
        return self.play_line(utt_id, speed)

    # ---------------------------------------------------------- settings

    def get_settings(self):
        yaml_doc = self._read_yaml()
        values = {}
        for section in SETTINGS_SCHEMA:
            for item in section["items"]:
                sec, key = item["path"].split(".")
                values[item["path"]] = (yaml_doc.get(sec) or {}).get(key)
        return {"schema": SETTINGS_SCHEMA, "values": values,
                "profile": yaml_doc.get("profile", "learning")}

    def set_setting(self, path: str, value):
        yaml_doc = self._read_yaml()
        sec, key = path.split(".")
        if sec not in yaml_doc or yaml_doc[sec] is None:
            yaml_doc[sec] = {}
        yaml_doc[sec][key] = value
        self._write_yaml(yaml_doc)
        return {"ok": True}

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

    def add_entry(self, uid, english):
        if self.api._overlay is not None:
            self.api._overlay.add_entry(uid, english)
        hist_id = self.api._pipeline.uid_to_hist.get(uid) if self.api._pipeline else None
        offset = ""
        if self.api._live_started_at:
            sec = int(time.time() - self.api._live_started_at)
            offset = f"{sec // 60:02d}:{sec % 60:02d}"
        self.api._push("line", {"uid": uid, "utt_id": hist_id, "en": english,
                                "offset": offset})

    def set_translation(self, uid, japanese):
        if self.api._overlay is not None:
            self.api._overlay.set_translation(uid, japanese)
        self.api._push("ja", {"uid": uid, "ja": japanese})
        status = self.api.get_status()
        if status.get("latency") is not None:
            self.api._push("latency", {"latency": status["latency"]})

    def set_suggestions(self, uid, pairs):
        self.api._push("suggest", {"uid": uid, "pairs": pairs})
