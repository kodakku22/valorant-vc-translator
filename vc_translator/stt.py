"""Speech-to-text with faster-whisper, tuned for short VC utterances."""

from __future__ import annotations

import logging
import os
import re
import sys

import numpy as np

log = logging.getLogger("stt")

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
                 no_speech_threshold: float = 0.6, hotwords: str = ""):
        from faster_whisper import WhisperModel

        self.beam_size = beam_size
        self.no_speech_threshold = no_speech_threshold
        self.hotwords = hotwords or None
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

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _info = self.model.transcribe(
            audio,
            language="en",
            beam_size=self.beam_size,
            condition_on_previous_text=False,
            no_speech_threshold=self.no_speech_threshold,
            hotwords=self.hotwords,
            vad_filter=False,  # segmentation is done upstream by SpeechSegmenter
            without_timestamps=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if self._is_junk(text):
            log.debug("filtered hallucination: %r", text)
            return ""
        return text

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
