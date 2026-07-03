"""Session history: SQLite records of every utterance plus optional audio clips.

Thread-safety: pipeline threads (stt / translate / suggest) all write here,
so every DB access goes through one connection guarded by a lock.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

log = logging.getLogger("history")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    profile TEXT
);
CREATE TABLE IF NOT EXISTS utterances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts TEXT NOT NULL,
    en TEXT NOT NULL,
    ja TEXT,
    audio_path TEXT,
    duration_s REAL,
    suggestions TEXT
);
CREATE INDEX IF NOT EXISTS idx_utt_session ON utterances(session_id);
"""


class HistoryStore:
    def __init__(self, data_dir: Path, save_audio: bool = True):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audio_root = self.data_dir / "audio"
        self.save_audio = save_audio
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.data_dir / "history.db"),
                                     check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.session_id: int | None = None
        self._session_dir: Path | None = None
        self._clip_no = 0

    # -- recording (called from pipeline threads) ---------------------------

    def start_session(self, profile: str) -> int:
        stamp = datetime.now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions (started_at, profile) VALUES (?, ?)",
                (stamp.isoformat(timespec="seconds"), profile))
            self._conn.commit()
            self.session_id = cur.lastrowid
        self._session_dir = self.audio_root / stamp.strftime("%Y%m%d_%H%M%S")
        self._clip_no = 0
        log.info("history session #%d started", self.session_id)
        return self.session_id

    def add_utterance(self, en: str, audio: np.ndarray | None = None,
                      duration_s: float = 0.0) -> int | None:
        if self.session_id is None:
            return None
        audio_path = None
        if self.save_audio and audio is not None and len(audio):
            try:
                audio_path = self._write_clip(audio)
            except Exception:
                log.exception("failed to write audio clip")
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO utterances (session_id, ts, en, audio_path, duration_s)"
                " VALUES (?, ?, ?, ?, ?)",
                (self.session_id, datetime.now().isoformat(timespec="seconds"),
                 en, audio_path, round(duration_s, 2)))
            self._conn.commit()
            return cur.lastrowid

    def set_translation(self, utt_id: int | None, ja: str):
        if utt_id is None:
            return
        with self._lock:
            self._conn.execute("UPDATE utterances SET ja = ? WHERE id = ?", (ja, utt_id))
            self._conn.commit()

    def set_suggestions(self, utt_id: int | None, suggestions: list):
        if utt_id is None:
            return
        with self._lock:
            self._conn.execute("UPDATE utterances SET suggestions = ? WHERE id = ?",
                               (json.dumps(suggestions, ensure_ascii=False), utt_id))
            self._conn.commit()

    def _write_clip(self, audio: np.ndarray) -> str:
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._clip_no += 1
        path = self._session_dir / f"{self._clip_no:04d}.wav"
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm.tobytes())
        return str(path)

    # -- browsing (called from the GUI) --------------------------------------

    def list_sessions(self) -> list[tuple]:
        """[(id, started_at, profile, utterance_count)] newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.id, s.started_at, s.profile, COUNT(u.id)"
                " FROM sessions s LEFT JOIN utterances u ON u.session_id = s.id"
                " GROUP BY s.id ORDER BY s.id DESC").fetchall()
        return rows

    def get_utterances(self, session_id: int) -> list[tuple]:
        """[(id, ts, en, ja, audio_path, suggestions)]"""
        with self._lock:
            return self._conn.execute(
                "SELECT id, ts, en, ja, audio_path, suggestions FROM utterances"
                " WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()

    def search(self, text: str, limit: int = 300) -> list[tuple]:
        """Search across all sessions. [(id, ts, en, ja, audio_path, suggestions)]"""
        pattern = f"%{text}%"
        with self._lock:
            return self._conn.execute(
                "SELECT id, ts, en, ja, audio_path, suggestions FROM utterances"
                " WHERE en LIKE ? OR ja LIKE ? ORDER BY id DESC LIMIT ?",
                (pattern, pattern, limit)).fetchall()

    def get_utterance(self, utt_id: int) -> tuple | None:
        with self._lock:
            return self._conn.execute(
                "SELECT id, ts, en, ja, audio_path, suggestions FROM utterances"
                " WHERE id = ?", (utt_id,)).fetchone()

    def random_with_audio(self) -> tuple | None:
        """Random utterance that has an audio clip (for listening/response drills)."""
        with self._lock:
            return self._conn.execute(
                "SELECT id, ts, en, ja, audio_path, suggestions FROM utterances"
                " WHERE audio_path IS NOT NULL ORDER BY RANDOM() LIMIT 1").fetchone()

    def random_phrases(self, n: int = 20) -> list[str]:
        """Random English phrases for reading practice."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT en FROM utterances WHERE length(en) > 8"
                " ORDER BY RANDOM() LIMIT ?", (n,)).fetchall()
        return [r[0] for r in rows]

    def delete_session(self, session_id: int):
        with self._lock:
            dirs = self._conn.execute(
                "SELECT DISTINCT audio_path FROM utterances"
                " WHERE session_id = ? AND audio_path IS NOT NULL",
                (session_id,)).fetchall()
            self._conn.execute("DELETE FROM utterances WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()
        clip_dirs = {Path(d[0]).parent for d in dirs}
        for d in clip_dirs:
            shutil.rmtree(d, ignore_errors=True)
        log.info("history session #%d deleted", session_id)

    def close(self):
        with self._lock:
            self._conn.close()
