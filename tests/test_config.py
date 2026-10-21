from __future__ import annotations

from pathlib import Path

from remastered.config import load_toml_config, resolve_settings


def test_load_toml_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[remastered]\nwork_dir = "/tmp/remastered-test"\ndefault_privacy = "unlisted"\n',
        encoding="utf-8",
    )
    data = load_toml_config(cfg)
    assert data["work_dir"] == "/tmp/remastered-test"
    assert data["default_privacy"] == "unlisted"


def test_resolve_settings_env_override(monkeypatch) -> None:
    monkeypatch.setenv("REMASTERED_DEFAULT_PRIVACY", "public")
    settings = resolve_settings(overrides={"bit_depth": 24})
    assert settings.default_privacy == "public"
    assert settings.bit_depth == 24


def test_resolve_settings_remastered_config_env(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("[remastered]\npreview = true\n", encoding="utf-8")
    monkeypatch.setenv("REMASTERED_CONFIG", str(cfg))
    settings = resolve_settings()
    assert settings.preview is True
