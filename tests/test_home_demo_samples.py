from __future__ import annotations

from pathlib import Path

import pytest

from remastered.web.home_demo_samples import resolve_home_demo_samples

_WEB_STATIC = Path(__file__).resolve().parents[1] / "src" / "remastered" / "web" / "static"
_SAMPLES = _WEB_STATIC / "samples"


def test_resolve_home_demo_samples_missing_by_default() -> None:
    assert resolve_home_demo_samples() is None


def test_resolve_home_demo_samples_from_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    samples = tmp_path / "samples"
    samples.mkdir()
    before = samples / "before.m4a"
    after = samples / "after.m4a"
    before.write_bytes(b"x" * 64)
    after.write_bytes(b"y" * 64)
    monkeypatch.setenv("REMASTERED_WEB_HOME_DEMO_BEFORE", str(before))
    monkeypatch.setenv("REMASTERED_WEB_HOME_DEMO_AFTER", str(after))
    monkeypatch.setattr(
        "remastered.web.home_demo_samples._STATIC_ROOT",
        tmp_path,
    )
    result = resolve_home_demo_samples()
    assert result is not None
    assert result.before_static_path == "samples/before.m4a"
    assert result.after_static_path == "samples/after.m4a"


def test_resolve_requires_both_stems() -> None:
    if not _SAMPLES.is_dir():
        pytest.skip("samples dir missing")
    # Repo ships no before/after audio — only README
    assert resolve_home_demo_samples() is None
