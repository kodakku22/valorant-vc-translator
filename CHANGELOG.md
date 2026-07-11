# Changelog

All notable changes to this project are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-07 (unreleased)

Accuracy & usability wave.

### Accuracy
- Instant translation cache: frequent short calls ("nice shot", "rotate B" …)
  render in ~0ms with stable wording (built-in stock phrases + LRU of past
  translations; long context-dependent lines are never cached).
- Audio pre-processing before recognition: 80 Hz high-pass + peak
  normalization, so quiet teammates are transcribed reliably (`stt.preprocess`).
- Idle-time retry: low-confidence (≈) lines are re-recognized at beam 5 while
  no one is talking, then the subtitle, translation and history update in place.
- Session vocabulary feedback: distinctive words confirmed during a match are
  added to the recognition hotwords for the rest of the session.

### Usability
- Global hotkeys that work while the game is focused: start/stop
  (Ctrl+Alt+T), star the last line (Ctrl+Alt+S) with on-overlay feedback, and
  show/hide the overlay (Ctrl+Alt+O). Configurable under `hotkeys:`.
- Drag-to-position overlay: a settings button lets you drag the subtitle
  window (double-click to confirm); offsets are saved automatically. Overlay
  settings (opacity, width, …) now apply to the live window immediately.
- Live transcript keeps your place: scrolling up pauses auto-follow and shows
  a "↓ 最新へ" chip; the bottom-anchored list is now actually scrollable
  (fixed a flexbox quirk that clipped older lines).
- Review reminder toast on launch when saved phrases are due.

## [1.0.0] — 2026-07 (unreleased)

First public release candidate: a fully local Valorant voice-chat translator
and English-learning app.

### Core
- Real-time pipeline: VB-Cable → Silero VAD → faster-whisper → Ollama → subtitles.
- Two-stage display (English immediately, Japanese ~0.5s later); ~0.6–0.9s total.
- `learning` / `ranked` profiles; glossary drives both recognition hotwords and
  fixed translations.

### Desktop app (Editorial Ink UI, pywebview)
- Live view with reply suggestions and a JA→EN callout box.
- Library / Review: session list, per-line playback (0.5/0.75/1×), star-to-save,
  shadowing scores, "missed" filter, session delete, history search.
- Flashcard review with spaced repetition using real match audio.
- Settings: config.yaml GUI with comment-preserving round-trip.
- Click-through subtitle overlay for use over the game.

### Recognition & translation accuracy
- Context-biased decoding (recent lines + domain terms as initial_prompt).
- Confidence surfacing: low-confidence lines marked and dimmed.
- Callout normalization ("H E" → "he", stutter trimming).
- Context-aware translation (pronoun/ellipsis resolution) + glossary enforcement.
- Background high-quality rescore of saved clips (beam 5 + word timestamps).

### Learning depth
- Per-phrase AI explanation (meaning / slang / usage).
- Word click-to-play from the saved clip.

### Robustness & quality
- Audio input auto-reconnect on device loss; Ollama auto-recovery mid-match.
- Uncaught-exception crash logs (`data/crash-*.log`).
- Setup checker + loopback audio test; first-run local-recording consent.
- pytest suite + GitHub Actions CI (compile, tests, lint, web-UI syntax).
- Packaged as a single Windows app via PyInstaller.

### Known limitations
- Mixed VC stream: no per-speaker separation.
- ~2s inherent latency to the Japanese translation.
- Unsigned build (SmartScreen "More info → Run" on first launch).
