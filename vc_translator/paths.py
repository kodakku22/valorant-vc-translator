"""Path resolution that works both from source and from a PyInstaller exe."""

from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    """Directory holding config.yaml / glossary.yaml / data/.

    Frozen (PyInstaller): the folder containing the exe, so users can edit
    config files next to it. From source: the project root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return app_root() / "config.yaml"


def glossary_path() -> Path:
    return app_root() / "glossary.yaml"


def data_dir() -> Path:
    d = app_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d
