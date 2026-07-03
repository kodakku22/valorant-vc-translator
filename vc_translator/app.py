"""Desktop application: control window with Live / History / Practice tabs.

Main thread owns all tkinter widgets (including the subtitle overlay, created
as a Toplevel). Worker threads post events into a queue drained by _poll().
"""

from __future__ import annotations

import difflib
import json
import logging
import queue
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk

log = logging.getLogger("app")

_DEFAULT_PHRASES = [
    "Two enemies pushing A short, I need backup.",
    "He's one shot, push him!",
    "Spike planted, rotate B, save your ult for retake.",
    "I'll smoke A main, wait for it.",
    "Watch the flank, they might lurk.",
    "Let's force buy this round, I'll drop you a vandal.",
    "One enemy heaven, one hell.",
    "Trade me if I die.",
    "They're saving, just play the spike.",
    "Nice shot! Let's take mid control.",
]


def _norm_words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z']+", text.lower()) if w]


class _PipelineUI:
    """Adapter handed to Pipeline: forwards to the overlay + the app event queue."""

    def __init__(self, app):
        self.app = app

    def add_entry(self, uid, english):
        if self.app.overlay is not None:
            self.app.overlay.add_entry(uid, english)
        self.app.post("en", uid, english)

    def set_translation(self, uid, japanese):
        if self.app.overlay is not None:
            self.app.overlay.set_translation(uid, japanese)
        self.app.post("ja", uid, japanese)

    def set_suggestions(self, uid, pairs):
        self.app.post("sug", uid, pairs)  # GUI only -- keep the overlay clean


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


class VCTranslatorApp:
    def __init__(self):
        from vc_translator import paths
        from vc_translator.config import load_config, load_glossary
        from vc_translator.history import HistoryStore

        self._load_config = load_config
        self._load_glossary = load_glossary
        self.paths = paths

        base_cfg = load_config(paths.config_path())
        hist_cfg = base_cfg.get("history", {})
        self.history = None
        if hist_cfg.get("enabled", True):
            self.history = HistoryStore(paths.data_dir(),
                                        save_audio=hist_cfg.get("save_audio", True))

        self.transcriber = None
        self.translator = None
        self.suggester = None
        self._components_key = None
        self.pipeline = None
        self.overlay = None
        self._recorder = None
        self._busy = False  # a model-load / practice job is running

        self._q: queue.Queue = queue.Queue()
        self._build_ui(default_profile=base_cfg.get("profile", "learning"),
                       mic_name=base_cfg.get("audio", {}).get("mic_name", ""))
        self.root.after(80, self._poll)

        import os
        if os.environ.get("VCT_AUTOTEST"):  # packaging self-test (see build docs)
            self._autotest_tries = 0
            self.root.after(1500, self._autotest_start)

    def _autotest_start(self):
        log.info("AUTOTEST: pressing start")
        self._on_start_stop()
        self.root.after(2000, self._autotest_check)

    def _autotest_check(self):
        if self.pipeline is not None:
            log.info("AUTOTEST: PIPELINE RUNNING OK")
            self.root.after(3000, self._autotest_finish)
            return
        self._autotest_tries += 1
        if self._autotest_tries > 90:
            log.error("AUTOTEST: TIMEOUT")
            self._on_close()
            return
        self.root.after(1000, self._autotest_check)

    def _autotest_finish(self):
        self._stop_pipeline()
        log.info("AUTOTEST: DONE")
        self.root.after(500, self._on_close)

    # ================= UI construction =================

    def _build_ui(self, default_profile: str, mic_name: str):
        self.root = tk.Tk()
        self.root.title("VC Translator — Valorant 英語VC翻訳 + 学習")
        self.root.geometry("1000x700")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # --- top bar ---
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(fill="x")
        self.start_btn = ttk.Button(bar, text="▶ 翻訳を開始", command=self._on_start_stop)
        self.start_btn.pack(side="left")
        ttk.Label(bar, text="プロファイル:").pack(side="left", padx=(16, 4))
        self.profile_var = tk.StringVar(value=default_profile)
        self.profile_box = ttk.Combobox(bar, textvariable=self.profile_var,
                                        values=["learning", "ranked"],
                                        state="readonly", width=10)
        self.profile_box.pack(side="left")
        self.status_var = tk.StringVar(value="停止中")
        ttk.Label(bar, textvariable=self.status_var, foreground="#555").pack(
            side="left", padx=16)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._build_live_tab(nb)
        self._build_history_tab(nb)
        self._build_practice_tab(nb, mic_name)
        self.notebook = nb

    def _build_live_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" ライブ ")
        self.live = tk.Text(tab, wrap="word", state="disabled", font=("Yu Gothic UI", 11))
        scroll = ttk.Scrollbar(tab, command=self.live.yview)
        self.live.configure(yscrollcommand=scroll.set)
        self.live.tag_configure("en", foreground="#666666")
        self.live.tag_configure("ja", font=("Yu Gothic UI", 12, "bold"))
        self.live.tag_configure("sug", foreground="#1a6fb0", lmargin1=24, lmargin2=36)
        self.live.tag_configure("callout", foreground="#0a7d40",
                                font=("Yu Gothic UI", 11, "bold"))
        self.live.tag_configure("info", foreground="#999999")

        bottom = ttk.Frame(tab, padding=(4, 6))
        bottom.pack(side="bottom", fill="x")
        ttk.Label(bottom, text="言いたいこと(日本語) →").pack(side="left")
        self.callout_entry = ttk.Entry(bottom)
        self.callout_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.callout_entry.bind("<Return>", lambda e: self._on_callout())
        self.callout_btn = ttk.Button(bottom, text="英語コールに変換", command=self._on_callout)
        self.callout_btn.pack(side="left")

        scroll.pack(side="right", fill="y")
        self.live.pack(fill="both", expand=True)

    def _build_history_tab(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text=" 履歴 ")

        top = ttk.Frame(tab, padding=4)
        top.pack(fill="x")
        ttk.Label(top, text="検索:").pack(side="left")
        self.search_entry = ttk.Entry(top, width=30)
        self.search_entry.pack(side="left", padx=4)
        self.search_entry.bind("<Return>", lambda e: self._on_search())
        ttk.Button(top, text="検索", command=self._on_search).pack(side="left")
        ttk.Button(top, text="クリア", command=self._refresh_history).pack(side="left", padx=4)
        ttk.Button(top, text="🔄 更新", command=self._refresh_history).pack(side="right")

        pane = ttk.PanedWindow(tab, orient="vertical")
        pane.pack(fill="both", expand=True)

        self.sess_tree = ttk.Treeview(pane, columns=("date", "count", "profile"),
                                      show="headings", height=5)
        for col, label, w in (("date", "セッション開始", 180), ("count", "発話数", 70),
                              ("profile", "プロファイル", 100)):
            self.sess_tree.heading(col, text=label)
            self.sess_tree.column(col, width=w, anchor="w")
        self.sess_tree.bind("<<TreeviewSelect>>", lambda e: self._on_session_select())
        pane.add(self.sess_tree, weight=1)

        mid = ttk.Frame(pane)
        self.utt_tree = ttk.Treeview(mid, columns=("time", "en", "ja"), show="headings")
        for col, label, w in (("time", "時刻", 130), ("en", "英語", 380), ("ja", "日本語", 380)):
            self.utt_tree.heading(col, text=label)
            self.utt_tree.column(col, width=w, anchor="w")
        utt_scroll = ttk.Scrollbar(mid, command=self.utt_tree.yview)
        self.utt_tree.configure(yscrollcommand=utt_scroll.set)
        self.utt_tree.bind("<<TreeviewSelect>>", lambda e: self._on_utt_select())
        utt_scroll.pack(side="right", fill="y")
        self.utt_tree.pack(fill="both", expand=True)
        pane.add(mid, weight=3)

        btns = ttk.Frame(tab, padding=4)
        btns.pack(fill="x")
        ttk.Button(btns, text="▶ 音声を再生", command=self._on_play).pack(side="left")
        ttk.Button(btns, text="💬 返答例を見る", command=self._on_show_suggestions).pack(
            side="left", padx=6)
        ttk.Button(btns, text="🗑 セッション削除", command=self._on_delete_session).pack(
            side="right")

        self.sug_text = tk.Text(tab, height=4, wrap="word", state="disabled",
                                font=("Yu Gothic UI", 10), background="#f4f7fa")
        self.sug_text.pack(fill="x", padx=4, pady=(0, 4))
        self._utt_rows: dict[str, tuple] = {}  # tree iid -> row tuple
        self._refresh_history()

    def _build_practice_tab(self, nb, mic_name: str):
        tab = ttk.Frame(nb, padding=8)
        nb.add(tab, text=" スピーキング練習 ")

        microw = ttk.Frame(tab)
        microw.pack(fill="x", pady=(0, 8))
        ttk.Label(microw, text="マイク:").pack(side="left")
        self.mic_var = tk.StringVar()
        self.mic_box = ttk.Combobox(microw, textvariable=self.mic_var, state="readonly",
                                    width=50)
        self.mic_box.pack(side="left", padx=6)
        ttk.Button(microw, text="🔄", width=3, command=lambda: self._load_mics(mic_name)).pack(
            side="left")
        self._mic_devices: list[tuple[int, str]] = []
        self._load_mics(mic_name)

        # --- Mode A: read-aloud ---
        boxa = ttk.LabelFrame(tab, text=" モード A: フレーズ音読(発音チェック) ", padding=8)
        boxa.pack(fill="x", pady=4)
        self.phrase_var = tk.StringVar(value="「新しいお題」を押してください")
        ttk.Label(boxa, textvariable=self.phrase_var, wraplength=880,
                  font=("Yu Gothic UI", 13, "bold")).pack(anchor="w")
        rowa = ttk.Frame(boxa)
        rowa.pack(fill="x", pady=6)
        ttk.Button(rowa, text="🎲 新しいお題", command=self._on_new_phrase).pack(side="left")
        self.reada_btn = ttk.Button(rowa, text="🎤 録音開始", command=self._on_record_read)
        self.reada_btn.pack(side="left", padx=8)
        self.reada_result = tk.Text(boxa, height=4, wrap="word", state="disabled",
                                    font=("Yu Gothic UI", 11))
        self.reada_result.tag_configure("ok", foreground="#0a7d40")
        self.reada_result.tag_configure("miss", foreground="#c0392b",
                                        font=("Yu Gothic UI", 11, "bold"))
        self.reada_result.pack(fill="x")

        # --- Mode B: scenario response ---
        boxb = ttk.LabelFrame(tab, text=" モード B: 実戦応答(聞いて→英語で返す) ", padding=8)
        boxb.pack(fill="x", pady=4)
        self.scenario_var = tk.StringVar(value="「お題を再生」で実際のVC音声が流れます")
        ttk.Label(boxb, textvariable=self.scenario_var, wraplength=880,
                  font=("Yu Gothic UI", 11)).pack(anchor="w")
        rowb = ttk.Frame(boxb)
        rowb.pack(fill="x", pady=6)
        ttk.Button(rowb, text="🔊 お題を再生", command=self._on_play_scenario).pack(side="left")
        self.readb_btn = ttk.Button(rowb, text="🎤 返答を録音", command=self._on_record_reply)
        self.readb_btn.pack(side="left", padx=8)
        self.readb_result = tk.Text(boxb, height=8, wrap="word", state="disabled",
                                    font=("Yu Gothic UI", 11))
        self.readb_result.pack(fill="both", expand=True)
        self._scenario_row = None  # current (id, ts, en, ja, audio_path, sug)

    # ================= event queue =================

    def post(self, kind, *payload):
        self._q.put((kind, payload))

    def _poll(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                handler = getattr(self, f"_ev_{kind}", None)
                if handler:
                    handler(*payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    # ================= start / stop =================

    def _on_start_stop(self):
        if self.pipeline is not None:
            self._stop_pipeline()
            return
        if self._busy:
            return
        self._busy = True
        self.start_btn.configure(state="disabled")
        self.profile_box.configure(state="disabled")
        self.status_var.set("モデル準備中...(初回は数十秒)")
        profile = self.profile_var.get()
        threading.Thread(target=self._prepare_worker, args=(profile,), daemon=True).start()

    def _prepare_worker(self, profile: str):
        try:
            cfg = self._load_config(self.paths.config_path(), profile)
            glossary = self._load_glossary(self.paths.glossary_path())
            self._ensure_components(cfg, glossary)
            self.post("start_ready", cfg, glossary)
        except Exception as exc:
            log.exception("preparation failed")
            self.post("start_failed", str(exc))

    def _ensure_components(self, cfg, glossary):
        """Build/warm heavy components once per (stt, translate) model pair."""
        from vc_translator.pipeline import build_components

        key = (cfg["stt"].get("model"), cfg["translate"].get("model"))
        if self._components_key == key and self.transcriber is not None:
            return
        self.post("status", f"音声認識モデル {key[0]} をロード中...")
        transcriber, translator, suggester = build_components(cfg, glossary)
        transcriber.warmup()
        self.post("status", "翻訳モデルを準備中...")
        if translator is not None:
            translator.warmup()
        self.transcriber, self.translator, self.suggester = transcriber, translator, suggester
        self._components_key = key

    def _ev_status(self, text):
        self.status_var.set(text)

    def _ev_start_failed(self, msg):
        self._busy = False
        self.start_btn.configure(state="normal")
        self.profile_box.configure(state="readonly")
        self.status_var.set("停止中")
        messagebox.showerror("起動エラー", msg)

    def _ev_start_ready(self, cfg, glossary):
        from vc_translator.audio import AudioCapture
        from vc_translator.overlay import SubtitleOverlay
        from vc_translator.pipeline import Pipeline

        self._busy = False
        try:
            audio_cfg = cfg.get("audio", {})
            source = AudioCapture(audio_cfg.get("device_name", "CABLE Output"),
                                  target_sr=int(audio_cfg.get("target_samplerate", 16000)),
                                  block_ms=int(audio_cfg.get("block_ms", 32)))
            self.overlay = SubtitleOverlay(cfg.get("overlay", {}), master=self.root)
            self.overlay.start_polling()
            self.pipeline = Pipeline(
                cfg, glossary, _PipelineUI(self), source,
                transcriber=self.transcriber, translator=self.translator,
                suggester=self.suggester, history=self.history)
            self.pipeline.start()
        except Exception as exc:
            log.exception("pipeline start failed")
            if self.overlay is not None:
                self.overlay.close()
                self.overlay = None
            self.pipeline = None
            self.start_btn.configure(state="normal")
            self.profile_box.configure(state="readonly")
            self.status_var.set("停止中")
            messagebox.showerror(
                "起動エラー",
                f"{exc}\n\nVB-Cable がインストールされているか、--list-devices で\n"
                "CABLE Output が見えるか確認してください。")
            return
        self.start_btn.configure(text="■ 停止", state="normal")
        self.status_var.set(f"稼働中({self.profile_var.get()})— 英語VCを待機しています")
        self._live_info("=== 翻訳を開始しました。味方の英語VCがここに表示されます ===")

    def _stop_pipeline(self):
        if self.pipeline is not None:
            self.pipeline.stop()
            self.pipeline = None
        if self.overlay is not None:
            self.overlay.close()
            self.overlay = None
        self.start_btn.configure(text="▶ 翻訳を開始")
        self.profile_box.configure(state="readonly")
        self.status_var.set("停止中")
        self._live_info("=== 停止しました ===")
        self._refresh_history()

    # ================= live tab =================

    def _live_append(self, text, tag, mark=None):
        self.live.configure(state="normal")
        if mark and mark in self.live.mark_names():
            self.live.insert(mark, text, tag)
        else:
            self.live.insert("end", text, tag)
        # keep the log bounded
        if int(self.live.index("end-1c").split(".")[0]) > 800:
            self.live.delete("1.0", "100.0")
        self.live.configure(state="disabled")
        self.live.see("end")

    def _live_info(self, msg):
        self._live_append(msg + "\n", "info")

    def _ev_en(self, uid, en):
        self._live_append(f"{en}\n", "en")
        self.live.mark_set(f"blk_{uid}", "end-1c")
        self.live.mark_gravity(f"blk_{uid}", "right")

    def _ev_ja(self, uid, ja):
        if ja:
            self._live_append(f"　{ja}\n", "ja", mark=f"blk_{uid}")

    def _ev_sug(self, uid, pairs):
        lines = "".join(f"💬 {en} — {ja}\n" for en, ja in pairs)
        self._live_append(lines, "sug", mark=f"blk_{uid}")

    def _on_callout(self):
        ja = self.callout_entry.get().strip()
        if not ja or self._busy:
            return
        self.callout_entry.delete(0, "end")
        self._live_append(f"\n[コール変換] {ja}\n", "info")
        threading.Thread(target=self._callout_worker, args=(ja,), daemon=True).start()

    def _callout_worker(self, ja):
        try:
            sug = self._get_suggester()
            pairs = sug.ja_to_callout(ja)
            self.post("callout_done", pairs)
        except Exception as exc:
            self.post("callout_done", [(f"(変換失敗: {exc})", "")])

    def _ev_callout_done(self, pairs):
        for en, ja in pairs:
            self._live_append(f"→ {en}", "callout")
            self._live_append(f"   {ja}\n" if ja else "\n", "info")

    def _get_suggester(self):
        """Suggester without loading Whisper (Ollama only, cheap)."""
        if self.suggester is not None:
            return self.suggester
        from vc_translator.suggest import Suggester
        from vc_translator.translate import OllamaChat

        cfg = self._load_config(self.paths.config_path(), self.profile_var.get())
        tr = cfg.get("translate", {})
        client = OllamaChat(tr.get("model", "gemma4:latest"),
                            host=tr.get("host", "http://127.0.0.1:11434"),
                            think=tr.get("think", False),
                            keep_alive=tr.get("keep_alive", -1),
                            temperature=float(tr.get("temperature", 0.2)),
                            timeout_s=float(tr.get("timeout_s", 30)))
        client.ensure_server()
        self.suggester = Suggester(client)
        return self.suggester

    # ================= history tab =================

    def _refresh_history(self):
        if self.history is None:
            return
        self.search_entry.delete(0, "end")
        self.sess_tree.delete(*self.sess_tree.get_children())
        for sid, started, profile, count in self.history.list_sessions():
            self.sess_tree.insert("", "end", iid=str(sid),
                                  values=(started.replace("T", " "), count, profile or ""))
        self._fill_utterances([])

    def _fill_utterances(self, rows):
        self.utt_tree.delete(*self.utt_tree.get_children())
        self._utt_rows.clear()
        for row in rows:
            utt_id, ts, en, ja, audio_path, sug = row
            time_part = ts.split("T")[1] if "T" in ts else ts
            iid = str(utt_id)
            self.utt_tree.insert("", "end", iid=iid, values=(time_part, en, ja or ""))
            self._utt_rows[iid] = row
        self._show_sug_text("")

    def _on_session_select(self):
        sel = self.sess_tree.selection()
        if sel and self.history is not None:
            self._fill_utterances(self.history.get_utterances(int(sel[0])))

    def _on_search(self):
        text = self.search_entry.get().strip()
        if text and self.history is not None:
            self._fill_utterances(self.history.search(text))

    def _selected_row(self):
        sel = self.utt_tree.selection()
        return self._utt_rows.get(sel[0]) if sel else None

    def _on_utt_select(self):
        row = self._selected_row()
        if row and row[5]:
            try:
                pairs = json.loads(row[5])
                self._show_sug_text("返答例:\n" + "\n".join(
                    f"  💬 {en} — {ja}" for en, ja in pairs))
                return
            except Exception:
                pass
        self._show_sug_text("")

    def _show_sug_text(self, text):
        self.sug_text.configure(state="normal")
        self.sug_text.delete("1.0", "end")
        self.sug_text.insert("end", text)
        self.sug_text.configure(state="disabled")

    def _on_play(self):
        row = self._selected_row()
        if row is None:
            return
        if not row[4]:
            messagebox.showinfo("再生", "この発話には音声が保存されていません。")
            return
        import winsound
        winsound.PlaySound(row[4], winsound.SND_FILENAME | winsound.SND_ASYNC)

    def _on_show_suggestions(self):
        row = self._selected_row()
        if row is None or self._busy:
            return
        if row[5]:
            self._on_utt_select()
            return
        self._show_sug_text("返答例を生成中...")
        threading.Thread(target=self._gen_sug_worker, args=(row,), daemon=True).start()

    def _gen_sug_worker(self, row):
        try:
            pairs = self._get_suggester().suggest_replies(row[2])
            if self.history is not None:
                self.history.set_suggestions(row[0], pairs)
            self.post("hist_sug_done", row[0], pairs)
        except Exception as exc:
            self.post("hist_sug_done", row[0], [(f"(生成失敗: {exc})", "")])

    def _ev_hist_sug_done(self, utt_id, pairs):
        iid = str(utt_id)
        if iid in self._utt_rows:
            row = list(self._utt_rows[iid])
            row[5] = json.dumps(pairs, ensure_ascii=False)
            self._utt_rows[iid] = tuple(row)
        self._show_sug_text("返答例:\n" + "\n".join(f"  💬 {en} — {ja}" for en, ja in pairs))

    def _on_delete_session(self):
        sel = self.sess_tree.selection()
        if not sel or self.history is None:
            return
        if messagebox.askyesno("確認", "このセッションの履歴と音声を削除しますか?"):
            self.history.delete_session(int(sel[0]))
            self._refresh_history()

    # ================= practice tab =================

    def _load_mics(self, prefer: str):
        import sounddevice as sd

        hostapis = sd.query_hostapis()
        self._mic_devices = []
        labels = []
        default_idx = 0
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] <= 0:
                continue
            if "WASAPI" not in hostapis[dev["hostapi"]]["name"]:
                continue
            if "cable" in dev["name"].lower():
                continue  # the VC tap is not a practice mic
            label = f"{dev['name']}"
            if prefer and prefer.lower() in dev["name"].lower():
                default_idx = len(self._mic_devices)
            self._mic_devices.append((i, label))
            labels.append(label)
        self.mic_box.configure(values=labels)
        if labels:
            self.mic_box.current(default_idx)

    def _mic_index(self) -> int | None:
        pos = self.mic_box.current()
        if pos < 0 or pos >= len(self._mic_devices):
            messagebox.showwarning("マイク", "マイクを選択してください。")
            return None
        return self._mic_devices[pos][0]

    def _practice_guard(self) -> bool:
        if self.pipeline is not None:
            messagebox.showinfo("練習モード",
                                "翻訳の稼働中は練習できません。先に「停止」してください。")
            return False
        if self._busy:
            return False
        return True

    def _on_new_phrase(self):
        phrases = self.history.random_phrases(5) if self.history else []
        import random
        self.phrase_var.set(random.choice(phrases or _DEFAULT_PHRASES))
        self._set_text(self.reada_result, "")

    def _on_record_read(self):
        if self._recorder is not None:
            # stop & grade
            audio = self._recorder.stop()
            self._recorder = None
            self.reada_btn.configure(text="🎤 録音開始")
            target = self.phrase_var.get()
            self._set_text(self.reada_result, "認識中...")
            self._busy = True
            threading.Thread(target=self._grade_worker, args=(target, audio),
                             daemon=True).start()
            return
        if not self._practice_guard():
            return
        if "お題" in self.phrase_var.get():
            self._on_new_phrase()
        idx = self._mic_index()
        if idx is None:
            return
        try:
            self._recorder = MicRecorder(idx)
            self._recorder.start()
        except Exception as exc:
            messagebox.showerror("マイク", f"録音を開始できません: {exc}")
            self._recorder = None
            return
        self.reada_btn.configure(text="■ 停止して判定")
        self._set_text(self.reada_result, "録音中... お題を読み上げてください")

    def _grade_worker(self, target: str, audio):
        try:
            self._ensure_stt_only()
            spoken = self.transcriber.transcribe(audio) if len(audio) else ""
            self.post("grade_done", target, spoken)
        except Exception as exc:
            self.post("grade_done", target, f"(認識失敗: {exc})")
        finally:
            self._busy = False

    def _ensure_stt_only(self):
        if self.transcriber is not None:
            return
        from vc_translator.pipeline import build_components

        self.post("status", "音声認識モデルをロード中...")
        cfg = self._load_config(self.paths.config_path(), self.profile_var.get())
        glossary = self._load_glossary(self.paths.glossary_path())
        transcriber, translator, suggester = build_components(cfg, glossary)
        transcriber.warmup()
        self.transcriber, self.translator = transcriber, translator
        if self.suggester is None:
            self.suggester = suggester
        self._components_key = (cfg["stt"].get("model"), cfg["translate"].get("model"))
        self.post("status", "停止中")

    def _ev_grade_done(self, target, spoken):
        widget = self.reada_result
        self._set_text(widget, "")
        widget.configure(state="normal")
        if not spoken or spoken.startswith("(認識失敗"):
            widget.insert("end", spoken or "(声を認識できませんでした。もう少し大きく話してみてください)")
            widget.configure(state="disabled")
            return
        t_words = _norm_words(target)
        s_words = _norm_words(spoken)
        matcher = difflib.SequenceMatcher(None, t_words, s_words)
        score = round(matcher.ratio() * 100)
        widget.insert("end", f"認識結果: {spoken}\n")
        widget.insert("end", f"一致度: {score}%   ")
        widget.insert("end", "◎ 完璧!\n" if score >= 90 else
                      "○ 十分伝わります\n" if score >= 70 else "△ もう一度挑戦!\n")
        widget.insert("end", "お題: ")
        for op, i1, i2, _j1, _j2 in matcher.get_opcodes():
            tag = "ok" if op == "equal" else "miss"
            for w in t_words[i1:i2]:
                widget.insert("end", w + " ", tag)
        widget.insert("end", "\n(赤い単語が聞き取ってもらえなかった部分)")
        widget.configure(state="disabled")

    def _on_play_scenario(self):
        if self.history is None:
            return
        row = self.history.random_with_audio()
        if row is None:
            messagebox.showinfo("お題", "音声付きの履歴がまだありません。まず翻訳を使ってVCを記録してください。")
            return
        self._scenario_row = row
        self.scenario_var.set("▶ 音声を再生しました。聞き取って、英語で返答を録音してください(もう一度押すと再再生)")
        self._set_text(self.readb_result, "")
        import winsound
        winsound.PlaySound(row[4], winsound.SND_FILENAME | winsound.SND_ASYNC)

    def _on_record_reply(self):
        if self._recorder is not None and self.readb_btn["text"].startswith("■"):
            audio = self._recorder.stop()
            self._recorder = None
            self.readb_btn.configure(text="🎤 返答を録音")
            self._set_text(self.readb_result, "認識・評価中...")
            self._busy = True
            threading.Thread(target=self._reply_worker,
                             args=(self._scenario_row, audio), daemon=True).start()
            return
        if not self._practice_guard():
            return
        if self._scenario_row is None:
            messagebox.showinfo("練習", "先に「お題を再生」を押してください。")
            return
        idx = self._mic_index()
        if idx is None:
            return
        try:
            self._recorder = MicRecorder(idx)
            self._recorder.start()
        except Exception as exc:
            messagebox.showerror("マイク", f"録音を開始できません: {exc}")
            self._recorder = None
            return
        self.readb_btn.configure(text="■ 停止して評価")
        self._set_text(self.readb_result, "録音中... 英語で返答してください")

    def _reply_worker(self, row, audio):
        try:
            self._ensure_stt_only()
            spoken = self.transcriber.transcribe(audio) if len(audio) else ""
            if not spoken:
                self.post("reply_done", row, "", "(声を認識できませんでした)")
                return
            fb = self._get_suggester().feedback(row[2], spoken)
            self.post("reply_done", row, spoken, fb)
        except Exception as exc:
            self.post("reply_done", row, "", f"(評価失敗: {exc})")
        finally:
            self._busy = False

    def _ev_reply_done(self, row, spoken, feedback):
        lines = [f"お題(味方の発言): {row[2]}"]
        if row[3]:
            lines.append(f"　訳: {row[3]}")
        if spoken:
            lines.append(f"\nあなたの返答: {spoken}\n")
        lines.append(feedback)
        self._set_text(self.readb_result, "\n".join(lines))

    # ================= misc =================

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def _on_close(self):
        try:
            if self.pipeline is not None:
                self.pipeline.stop()
            if self.history is not None:
                self.history.close()
        finally:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def run_app():
    VCTranslatorApp().run()
