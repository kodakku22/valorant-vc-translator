"""Entry point.

No arguments -> GUI application (the mode the packaged exe uses).
CLI flags (--console / --test-file / --list-devices ...) keep the original
pipeline-only behavior for testing and debugging.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from vc_translator import paths


def _setup_logging(verbose: bool):
    handlers = None
    if sys.stderr is None:  # windowed exe: no console to write to
        handlers = [logging.FileHandler(paths.data_dir() / "app.log", encoding="utf-8")]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s.%(msecs)03d %(name)-9s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers)


def main():
    parser = argparse.ArgumentParser(
        prog="vc_translator",
        description="Valorant VC real-time translator (EN VC -> EN/JA subtitles, fully local)")
    parser.add_argument("--config", default=str(paths.config_path()))
    parser.add_argument("--glossary", default=str(paths.glossary_path()))
    parser.add_argument("--profile", help="config profile (learning / ranked)")
    parser.add_argument("--gui", action="store_true", help="launch the GUI app (default)")
    parser.add_argument("--console", action="store_true",
                        help="CLI mode: print subtitles to the console")
    parser.add_argument("--overlay", action="store_true",
                        help="CLI mode: subtitle overlay only, no GUI app")
    parser.add_argument("--test-file", metavar="WAV",
                        help="CLI mode: run the pipeline on a WAV file")
    parser.add_argument("--realtime", action="store_true",
                        help="with --test-file: play the file at real-time speed")
    parser.add_argument("--list-devices", action="store_true",
                        help="list audio input devices and exit")
    parser.add_argument("--stt-model", help="override STT model (e.g. tiny/base for quick tests)")
    parser.add_argument("--stt-device", choices=["cuda", "cpu"], help="override STT device")
    parser.add_argument("--no-translate", action="store_true",
                        help="skip translation (STT only)")
    parser.add_argument("--no-history", action="store_true",
                        help="do not record history in CLI modes")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    if args.list_devices:
        from vc_translator.audio import list_input_devices
        print(list_input_devices())
        return

    cli_mode = args.console or args.overlay or args.test_file
    if not cli_mode:
        from vc_translator.app import run_app
        run_app()
        return

    # ---- CLI pipeline modes (testing / debugging) ----
    from vc_translator.config import load_config, load_glossary
    cfg = load_config(args.config, args.profile)
    glossary = load_glossary(args.glossary)
    if args.stt_model:
        cfg["stt"]["model"] = args.stt_model
    if args.stt_device:
        cfg["stt"]["device"] = args.stt_device

    audio_cfg = cfg.get("audio", {})
    target_sr = int(audio_cfg.get("target_samplerate", 16000))
    block_ms = int(audio_cfg.get("block_ms", 32))
    if args.test_file:
        from vc_translator.audio import FileCapture
        source = FileCapture(args.test_file, target_sr=target_sr, block_ms=block_ms,
                             realtime=args.realtime)
    else:
        from vc_translator.audio import AudioCapture
        source = AudioCapture(audio_cfg.get("device_name", "CABLE Output"),
                              target_sr=target_sr, block_ms=block_ms)

    if args.overlay and not args.test_file:
        from vc_translator.overlay import SubtitleOverlay
        ui = SubtitleOverlay(cfg.get("overlay", {}))
    else:
        from vc_translator.overlay import ConsoleUI
        ui = ConsoleUI()

    history = None
    if cfg.get("history", {}).get("enabled", True) and not args.no_history:
        from vc_translator.history import HistoryStore
        history = HistoryStore(paths.data_dir(),
                               save_audio=cfg["history"].get("save_audio", True))

    from vc_translator.pipeline import Pipeline
    pipeline = Pipeline(cfg, glossary, ui, source, history=history,
                        no_translate=args.no_translate)
    pipeline.start()

    # In file mode, close the UI automatically once everything is processed.
    if args.test_file:
        def _wait_done():
            pipeline.done_event.wait()
            ui.close()
        threading.Thread(target=_wait_done, daemon=True).start()

    try:
        ui.run()  # blocks (tk mainloop / console wait)
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()
        if history is not None:
            history.close()
        logging.getLogger("main").info("stopped")


if __name__ == "__main__":
    main()
