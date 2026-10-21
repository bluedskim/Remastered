from __future__ import annotations

from pathlib import Path

from remastered.master import run_matchering


def test_matchering_synthetic_wav(tone_pair: tuple[Path, Path], tmp_path: Path) -> None:
    target, reference = tone_pair
    result = run_matchering(target, reference, tmp_path / "out", bit_depth=16, preview=True)
    assert result.output_path.is_file()
    assert result.output_path.stat().st_size > 1000
    assert result.preview_path is not None
    assert result.preview_path.is_file()


def test_matchering_24bit(tone_pair: tuple[Path, Path], tmp_path: Path) -> None:
    target, reference = tone_pair
    result = run_matchering(target, reference, tmp_path / "out24", bit_depth=24)
    assert "24" in result.output_path.name
    assert result.output_path.is_file()
