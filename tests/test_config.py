import pytest

from vc_translator.config import ConfigError, load_config, load_glossary
from vc_translator import paths


def test_profile_merge(work_config):
    cfg = load_config(work_config / "config.yaml", "ranked")
    # ranked overrides stt.model and turns suggest off
    assert cfg["stt"]["model"] == "large-v3-turbo"
    assert cfg["suggest"]["live"] is False
    # base sections still present
    assert cfg["audio"]["device_name"]


def test_unknown_profile_raises_configerror(work_config):
    with pytest.raises(ConfigError):
        load_config(work_config / "config.yaml", "does-not-exist")


def test_glossary_has_terms_and_hotwords(work_config):
    g = load_glossary(work_config / "glossary.yaml")
    assert g["hotwords"]
    assert g["terms"].get("rotate")


def test_missing_glossary_is_safe(tmp_path):
    g = load_glossary(tmp_path / "nope.yaml")
    assert g == {"hotwords": "", "terms": {}}


def test_setting_scope_global_vs_profile(work_config):
    """set_setting: global keys -> base (both profiles); overridden keys -> profile."""
    from vc_translator import bridge

    api = bridge.Api()
    try:
        api._profile = "learning"
        api.set_setting("audio.device_name", "MyCable")   # not overridden by any profile
        api.set_setting("stt.model", "medium")            # learning overrides stt.model
        eff_l = load_config(work_config / "config.yaml", "learning")
        eff_r = load_config(work_config / "config.yaml", "ranked")
        assert eff_l["audio"]["device_name"] == "MyCable"
        assert eff_r["audio"]["device_name"] == "MyCable"       # global -> both
        assert eff_l["stt"]["model"] == "medium"
        assert eff_r["stt"]["model"] == "large-v3-turbo"        # no leak
        # comments preserved
        text = (work_config / "config.yaml").read_text(encoding="utf-8")
        assert "誤検出が多ければ上げる" in text
    finally:
        api._history.close()
