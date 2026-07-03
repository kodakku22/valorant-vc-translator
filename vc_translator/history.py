"""Session history: SQLite records of every utterance plus optional audio clips.

Schema v2 adds learning features on top of the v1 transcript log:
  - utterances.starred          -- saved phrases (feed the review queue)
  - cards                       -- spaced-repetition metadata per saved phrase
  - plays                       -- playback log (drives the "聞き逃し" filter)
  - sessions.ended_at/reviewed_at

Thread-safety: pipeline threads (stt / translate / suggest) and the GUI bridge
all write here, so every DB access goes through one connection guarded by a lock.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import threading
import wave
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

log = logging.getLogger("history")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    profile TEXT,
    ended_at TEXT,
    reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS utterances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts TEXT NOT NULL,
    en TEXT NOT NULL,
    ja TEXT,
    audio_path TEXT,
    duration_s REAL,
    suggestions TEXT,
    starred INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    utt_id INTEGER NOT NULL REFERENCES utterances(id),
    en TEXT NOT NULL,
    ja TEXT,
    created_ts TEXT NOT NULL,
    due_ts TEXT NOT NULL,
    interval_h REAL DEFAULT 0,
    streak INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    utt_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    speed REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_utt_session ON utterances(session_id);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(due_ts);
CREATE INDEX IF NOT EXISTS idx_plays_utt ON plays(utt_id);
"""

# columns added after the first release -- applied to pre-existing DBs
_MIGRATIONS = [
    "ALTER TABLE utterances ADD COLUMN starred INTEGER DEFAULT 0",
    "ALTER TABLE sessions ADD COLUMN ended_at TEXT",
    "ALTER TABLE sessions ADD COLUMN reviewed_at TEXT",
]

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "i", "im", "i'm", "you", "he", "she",
    "we", "they", "it", "is", "are", "was", "be", "to", "of", "in", "on", "at",
    "for", "with", "up", "down", "out", "my", "your", "his", "her", "their",
    "me", "him", "them", "us", "this", "that", "there", "here", "let", "lets",
    "let's", "dont", "don't", "not", "no", "yes", "so", "just", "now", "get",
    "got", "go", "going", "gonna", "can", "will", "have", "has", "need", "one",
    "guys", "guy", "they're", "theyre", "all",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()
        self.session_id: int | None = None
        self._session_dir: Path | None = None
        self._clip_no = 0

    # ================= recording (pipeline threads) =================

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

    def end_session(self):
        if self.session_id is None:
            return
        with self._lock:
            self._conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?",
                               (_now(), self.session_id))
            # discard empty sessions so they don't clutter the library
            count = self._conn.execute(
                "SELECT COUNT(*) FROM utterances WHERE session_id = ?",
                (self.session_id,)).fetchone()[0]
            if count == 0:
                self._conn.execute("DELETE FROM sessions WHERE id = ?", (self.session_id,))
            self._conn.commit()
        log.info("history session #%d ended (%d lines)", self.session_id, count)
        self.session_id = None

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
                (self.session_id, _now(), en, audio_path, round(duration_s, 2)))
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

    # ================= stars / cards (SRS) =================

    def toggle_star(self, utt_id: int) -> bool:
        """Flip the star; starring creates a review card, unstarring removes it."""
        with self._lock:
            row = self._conn.execute(
                "SELECT starred, en, ja FROM utterances WHERE id = ?", (utt_id,)).fetchone()
            if row is None:
                return False
            starred, en, ja = row
            new = 0 if starred else 1
            self._conn.execute("UPDATE utterances SET starred = ? WHERE id = ?",
                               (new, utt_id))
            if new:
                self._conn.execute(
                    "INSERT INTO cards (utt_id, en, ja, created_ts, due_ts)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (utt_id, en, ja, _now(), _now()))  # due immediately
            else:
                self._conn.execute("DELETE FROM cards WHERE utt_id = ?", (utt_id,))
            self._conn.commit()
            return bool(new)

    def due_cards(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id, c.utt_id, c.en, u.ja, u.audio_path, c.streak, u.ts,"
                "       u.session_id"
                " FROM cards c JOIN utterances u ON u.id = c.utt_id"
                " WHERE c.due_ts <= ? ORDER BY c.due_ts", (_now(),)).fetchall()
        return [{"card_id": r[0], "utt_id": r[1], "en": r[2], "ja": r[3] or "",
                 "audio_path": r[4], "streak": r[5], "ts": r[6], "session_id": r[7]}
                for r in rows]

    def due_count(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM cards WHERE due_ts <= ?", (_now(),)).fetchone()[0]

    def answer_card(self, card_id: int, ok: bool):
        """Simplified SM-2: ok -> grow interval (4h, then x2.5); ng -> 10 min."""
        with self._lock:
            row = self._conn.execute(
                "SELECT interval_h, streak FROM cards WHERE id = ?", (card_id,)).fetchone()
            if row is None:
                return
            interval_h, streak = row
            if ok:
                streak += 1
                interval_h = 4.0 if streak <= 1 else interval_h * 2.5
            else:
                streak = 0
                interval_h = 10 / 60
            due = (datetime.now() + timedelta(hours=interval_h)).isoformat(timespec="seconds")
            self._conn.execute(
                "UPDATE cards SET interval_h = ?, streak = ?, due_ts = ? WHERE id = ?",
                (interval_h, streak, due, card_id))
            self._conn.commit()

    # ================= playback log =================

    def log_play(self, utt_id: int, speed: float):
        with self._lock:
            self._conn.execute("INSERT INTO plays (utt_id, ts, speed) VALUES (?, ?, ?)",
                               (utt_id, _now(), speed))
            self._conn.commit()

    # ================= browsing (GUI bridge) =================

    def list_sessions(self) -> list[dict]:
        """Session summaries, newest first, with per-minute density for the mini bars."""
        with self._lock:
            sessions = self._conn.execute(
                "SELECT id, started_at, profile, ended_at, reviewed_at"
                " FROM sessions ORDER BY id DESC").fetchall()
            out = []
            for sid, started, profile, ended, reviewed in sessions:
                rows = self._conn.execute(
                    "SELECT ts, starred FROM utterances WHERE session_id = ? ORDER BY id",
                    (sid,)).fetchall()
                if not rows:
                    continue
                start_dt = datetime.fromisoformat(started)
                last_dt = datetime.fromisoformat(rows[-1][0])
                end_dt = datetime.fromisoformat(ended) if ended else last_dt
                minutes = max(1, int((end_dt - start_dt).total_seconds() // 60) + 1)
                density = [0] * min(minutes, 60)
                star_bins = set()
                for ts, starred in rows:
                    m = int((datetime.fromisoformat(ts) - start_dt).total_seconds() // 60)
                    m = min(m, len(density) - 1)
                    if m >= 0:
                        density[m] += 1
                        if starred:
                            star_bins.add(m)
                out.append({
                    "id": sid, "started_at": started, "profile": profile or "",
                    "minutes": minutes, "lines": len(rows),
                    "stars": sum(1 for _, s in rows if s),
                    "reviewed": bool(reviewed),
                    "density": density, "star_bins": sorted(star_bins),
                })
        return out

    def get_session(self, session_id: int) -> list[dict]:
        """All lines of one session with star + playback stats."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT u.id, u.ts, u.en, u.ja, u.audio_path, u.starred, u.duration_s,"
                "       (SELECT COUNT(*) FROM plays p WHERE p.utt_id = u.id),"
                "       (SELECT MIN(speed) FROM plays p WHERE p.utt_id = u.id)"
                " FROM utterances u WHERE u.session_id = ? ORDER BY u.id",
                (session_id,)).fetchall()
            started = self._conn.execute(
                "SELECT started_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
        start_dt = datetime.fromisoformat(started[0]) if started else None
        out = []
        for uid, ts, en, ja, audio, starred, dur, plays, min_speed in rows:
            offset = ""
            if start_dt is not None:
                sec = int((datetime.fromisoformat(ts) - start_dt).total_seconds())
                offset = f"{sec // 60:02d}:{sec % 60:02d}"
            missed = (plays or 0) >= 2 or (min_speed is not None and min_speed <= 0.75)
            out.append({"id": uid, "ts": ts, "offset": offset, "en": en, "ja": ja or "",
                        "audio_path": audio, "starred": bool(starred),
                        "duration_s": dur or 0, "missed": missed})
        return out

    def frequent_terms(self, session_id: int, top: int = 6) -> list[tuple[str, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT en FROM utterances WHERE session_id = ?", (session_id,)).fetchall()
        counts: dict[str, int] = {}
        for (en,) in rows:
            for w in re.split(r"[^a-zA-Z']+", en.lower()):
                if len(w) >= 4 and w not in _STOPWORDS:
                    counts[w] = counts.get(w, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        return [(w, n) for w, n in ranked[:top] if n >= 2]

    def saved_in_session(self, session_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, en, ja FROM utterances WHERE session_id = ? AND starred = 1"
                " ORDER BY id", (session_id,)).fetchall()
        return [{"id": r[0], "en": r[1], "ja": r[2] or ""} for r in rows]

    def mark_reviewed(self, session_id: int):
        with self._lock:
            self._conn.execute("UPDATE sessions SET reviewed_at = ? WHERE id = ?",
                               (_now(), session_id))
            self._conn.commit()

    def get_utterance(self, utt_id: int) -> dict | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT id, ts, en, ja, audio_path, session_id FROM utterances"
                " WHERE id = ?", (utt_id,)).fetchone()
        if r is None:
            return None
        return {"id": r[0], "ts": r[1], "en": r[2], "ja": r[3] or "",
                "audio_path": r[4], "session_id": r[5]}

    def search(self, text: str, limit: int = 300) -> list[tuple]:
        pattern = f"%{text}%"
        with self._lock:
            return self._conn.execute(
                "SELECT id, ts, en, ja, audio_path, suggestions FROM utterances"
                " WHERE en LIKE ? OR ja LIKE ? ORDER BY id DESC LIMIT ?",
                (pattern, pattern, limit)).fetchall()

    def random_phrases(self, n: int = 20) -> list[str]:
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
            self._conn.execute(
                "DELETE FROM cards WHERE utt_id IN"
                " (SELECT id FROM utterances WHERE session_id = ?)", (session_id,))
            self._conn.execute(
                "DELETE FROM plays WHERE utt_id IN"
                " (SELECT id FROM utterances WHERE session_id = ?)", (session_id,))
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
