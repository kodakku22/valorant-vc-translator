"""Configuration loading: config.yaml + glossary.yaml, with profile overlay."""

from __future__ import annotations

import copy
import logging
from pathlib import Path

import yaml

log = logging.getLogger("config")

# Sections a profile may override.
_PROFILE_SECTIONS = ("audio", "vad", "stt", "translate", "overlay", "history", "suggest")


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_config(config_path: str | Path, profile: str | None = None) -> dict:
    config_path = Path(config_path)
    with config_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    active = profile or raw.get("profile") or "learning"
    profiles = raw.get("profiles") or {}
    overrides = profiles.get(active)
    if overrides is None:
        available = ", ".join(profiles) or "(none)"
        raise SystemExit(f"profile '{active}' not found in {config_path} (available: {available})")

    cfg = {section: raw.get(section, {}) or {} for section in _PROFILE_SECTIONS}
    cfg = _deep_merge(cfg, {k: v for k, v in overrides.items() if k in _PROFILE_SECTIONS})
    cfg["profile"] = active
    log.info("profile: %s (stt=%s, translate=%s)", active,
             cfg["stt"].get("model"), cfg["translate"].get("model"))
    return cfg


def load_glossary(glossary_path: str | Path) -> dict:
    glossary_path = Path(glossary_path)
    if not glossary_path.exists():
        log.warning("glossary not found: %s (continuing without it)", glossary_path)
        return {"hotwords": "", "terms": {}}
    with glossary_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    hotwords = " ".join(str(raw.get("hotwords", "")).split())
    terms = raw.get("terms") or {}
    return {"hotwords": hotwords, "terms": terms}
