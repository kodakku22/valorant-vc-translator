"""Shared pytest fixtures. Keeps the project importable without heavy deps."""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_data(tmp_path):
    """A fresh data dir for HistoryStore."""
    return tmp_path


@pytest.fixture
def work_config(tmp_path):
    """A temp copy of config.yaml + glossary.yaml, with paths.app_root redirected."""
    import shutil

    from vc_translator import paths
    shutil.copy(ROOT / "config.yaml", tmp_path / "config.yaml")
    shutil.copy(ROOT / "glossary.yaml", tmp_path / "glossary.yaml")
    orig = paths.app_root
    paths.app_root = lambda: tmp_path
    yield tmp_path
    paths.app_root = orig
