"""Speech-to-text with faster-whisper, tuned for short VC utterances."""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field

import numpy as np

from vc_translator.normalize import normalize_callout

log = logging.getLogger("stt")

_PROMPT_MAX_CHARS = 700  # keep the initial_prompt well under Whisper's token cap


@dataclass
class TranscriptResult:
    text: str = ""
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    low_confidence: bool = False
    words: list = field(default_factory=list)  # [(word, start, end)] when requested

# Whisper's well-known hallucinations on silence/noise-only input.
_JUNK_EXACT = {
    "you", "you.", "bye.", "bye bye.", "thank you.", "thanks.", "thank you",
    ".", "the", "uh", "um", "oh", "yeah.", "yeah",
}
_JUNK_SUBSTRINGS = (
    "thank you for watching",
    "thanks for watching",
    "subscribe",
    "www.",
    "copyright",
    "amara.org",
)


def _enable_cuda_dlls():
    """CTranslate2 on Windows needs cuBLAS/cuDNN DLLs; a CUDA build of torch bundles them."""
    if sys.platform != "win32":
        return
    try:
        import torch
        lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(lib):
            os.add_dll_directory(lib)
    except Exception:
        pass


class Transcriber:
    def __init__(self, model: str = "large-v3", device: str = "cuda",
                 compute_type: str = "int8_float16", beam_size: int = 2,
                 no_speech_threshold: float = 0.6, hotwords: str = "",
                 min_avg_logprob: float = -1.0):
        from faster_whisper import WhisperModel

        self.beam_size = beam_size
        self.no_speech_threshold = no_speech_threshold
        self.hotwords = hotwords or ""
        self.min_avg_logprob = min_avg_logprob  # below this -> low_confidence
        self._model_name = model

        if device == "cuda":
            _enable_cuda_dlls()
        elif "float16" in compute_type:
            compute_type = "int8"  # float16 kernels are GPU-only
        try:
            log.info("loading whisper model '%s' on %s (%s)...", model, device, compute_type)
            self.model = WhisperModel(model, device=device, compute_type=compute_type)
        except Exception as exc:
            if device == "cuda":
                log.warning("CUDA init failed (%s) -- falling back to CPU int8", exc)
                self.model = WhisperModel(model, device="cpu", compute_type="int8")
            else:
                raise
        log.info("whisper model ready")

    def warmup(self):
        """Run one dummy pass so CUDA kernels/JIT costs are paid before the match starts."""
        try:
            self.transcribe(np.zeros(16000, dtype=np.float32))
        except Exception as exc:
            log.warning("warmup on current device failed (%s) -- falling back to CPU int8", exc)
            from faster_whisper import WhisperModel
            self.model = WhisperModel(self._model_name, device="cpu", compute_type="int8")
            self.transcribe(np.zeros(16000, dtype=np.float32))
        log.info("stt warmup done")

    def _build_prompt(self, context: str) -> str:
        """initial_prompt = domain hotwords + recent context.

        Whisper keeps the tail of the prompt when it exceeds the token cap, so
        the recent context (most useful) goes last and survives truncation."""
        parts = []
        if self.hotwords:
            parts.append(self.hotwords)
        if context:
            parts.append(context)
        prompt = ". ".join(parts).strip()
        if len(prompt) > _PROMPT_MAX_CHARS:
            prompt = prompt[-_PROMPT_MAX_CHARS:]
        return prompt or None

    def transcribe(self, audio: np.ndarray, context: str = "",
                   beam_size: int | None = None, word_timestamps: bool = False):
        """Return a TranscriptResult. `context` biases decoding toward recent
        lines (A1); higher `beam_size` trades speed for accuracy (A3)."""
        segments, _info = self.model.transcribe(
            audio,
            language="en",
            beam_size=beam_size or self.beam_size,
            condition_on_previous_text=False,
            no_speech_threshold=self.no_speech_threshold,
            initial_prompt=self._build_prompt(context),
            vad_filter=False,  # segmentation is done upstream by SpeechSegmenter
            without_timestamps=not word_timestamps,
            word_timestamps=word_timestamps,
        )
        seg_list = list(segments)
        text = normalize_callout(" ".join(s.text.strip() for s in seg_list).strip())
        if self._is_junk(text):
            log.debug("filtered hallucination: %r", text)
            return TranscriptResult(text="")

        # length-weighted average log-prob as a confidence proxy
        total = sum(max(1, len(s.text)) for s in seg_list) or 1
        avg_lp = sum(s.avg_logprob * max(1, len(s.text)) for s in seg_list) / total
        nsp = max((s.no_speech_prob for s in seg_list), default=0.0)
        words = []
        if word_timestamps:
            for s in seg_list:
                for w in (s.words or []):
                    words.append((w.word.strip(), round(w.start, 3), round(w.end, 3)))
        return TranscriptResult(
            text=text, avg_logprob=avg_lp, no_speech_prob=nsp,
            low_confidence=avg_lp < self.min_avg_logprob, words=words)

    @staticmethod
    def _is_junk(text: str) -> bool:
        if not text:
            return True
        lowered = text.lower().strip()
        if lowered in _JUNK_EXACT:
            return True
        if any(pattern in lowered for pattern in _JUNK_SUBSTRINGS):
            return True
        # Repetition hallucinations like "you, you, you, you" -- junk words only.
        words = [w for w in re.split(r"[^a-z']+", lowered) if w]
        junk_words = {"you", "uh", "um", "oh", "the", "bye", "thank", "thanks", "yeah"}
        return bool(words) and all(w in junk_words for w in words)
