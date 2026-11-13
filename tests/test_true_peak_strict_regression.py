"""Strict PR80 guard: mocked edge/stress (not audible-quality proof)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from remastered.web.settings import WebSettings
from remastered.web.true_peak_protection import apply_final_true_peak_protection, pass_ceiling_dbtp


def test_legacy_headroom_env_means_stricter_pass_ceiling_not_aac_relax() -> None:
    ws = WebSettings(true_peak_ceiling_dbtp=-1.0, true_peak_pass_headroom_db=0.5)
    assert pass_ceiling_dbtp(ws) == pytest.approx(-1.5)


def test_wav_safe_aac_unsafe_does_not_early_pass() -> None:
    """WAV under ceiling but AAC -0.7 must force preview attenuation (not WAV)."""
    peaks = [2.0, -1.1, -0.7, -2.5]

    def fake_measure(path: Path, **kwargs):
        return peaks.pop(0)

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("remastered.web.true_peak_protection._apply_gain_wav"),
        patch("shutil.copy2"),
    ):
        wav_gain, pw, pa, preview_extra = apply_final_true_peak_protection(
            Path("in.wav"),
            Path("out.wav"),
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.0,
                true_peak_max_attempts=8,
                true_peak_refine_max_attempts=0,
            ),
        )
    assert wav_gain < 0
    assert preview_extra < 0
    assert pw <= -1.0
    assert pa <= -1.0
    assert len(peaks) == 0


def test_non_monotonic_aac_overshoot_after_wav_improves() -> None:
    """Preview AAC can stay hot while WAV is already safe; preview loop keeps stepping."""
    peaks = [1.0, -1.2, 1.5, 0.2, -2.0, -1.2]

    def fake_measure(path: Path, **kwargs):
        return peaks.pop(0)

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("remastered.web.true_peak_protection._apply_gain_wav"),
        patch("shutil.copy2"),
    ):
        wav_gain, pw, pa, preview_extra = apply_final_true_peak_protection(
            Path("in.wav"),
            Path("out.wav"),
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.05,
                true_peak_max_attempts=12,
                true_peak_refine_max_attempts=0,
            ),
        )
    assert wav_gain < 0
    assert preview_extra < 0
    assert pw <= pass_ceiling_dbtp(WebSettings())
    assert pa <= pass_ceiling_dbtp(WebSettings())


def test_aac_measurement_failure_restores_raw(tmp_path: Path) -> None:
    wav_in = tmp_path / "in.wav"
    wav_out = tmp_path / "out.wav"
    wav_in.write_bytes(b"RAW")
    wav_out.write_bytes(b"RAW")

    calls = {"n": 0}

    def fake_measure(path: Path, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return 1.0
        if ".m4a" in path.suffix.lower():
            return None
        return -2.0

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("remastered.web.true_peak_protection._apply_gain_wav"),
    ):
        with pytest.raises(RuntimeError, match="measurement failed \\(AAC\\)"):
            apply_final_true_peak_protection(wav_in, wav_out, ws=WebSettings())
    assert wav_out.read_bytes() == b"RAW"


def test_encode_failure_restores_raw(tmp_path: Path) -> None:
    wav_in = tmp_path / "in.wav"
    wav_out = tmp_path / "out.wav"
    wav_in.write_bytes(b"RAW2")
    wav_out.write_bytes(b"RAW2")

    def fake_measure(path: Path, **kwargs):
        if path == wav_in:
            return 0.5
        return -2.0

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch(
            "remastered.web.true_peak_protection._encode_playback_aac",
            side_effect=RuntimeError("encode failed"),
        ),
        patch("remastered.web.true_peak_protection._apply_gain_wav"),
    ):
        with pytest.raises(RuntimeError, match="encode failed"):
            apply_final_true_peak_protection(wav_in, wav_out, ws=WebSettings())
    assert wav_out.read_bytes() == b"RAW2"


def test_pass_at_pass_ceiling_margin_not_above() -> None:
    ws = WebSettings(true_peak_pass_headroom_db=0.05)
    ceil = pass_ceiling_dbtp(ws)
    peaks = [-1.06, -1.06, -1.06]

    def fake_measure(path: Path, **kwargs):
        return peaks.pop(0)

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("shutil.copy2"),
    ):
        g, pw, pa, _ = apply_final_true_peak_protection(Path("a"), Path("b"), ws=ws)
    assert g == 0.0
    assert pw <= ceil
    assert pa <= ceil


def test_minus_1_04_wav_rejected_with_zero_margin() -> None:
    peaks = [0.5, -1.04, -2.0, -2.0]

    def fake_measure(path: Path, **kwargs):
        return peaks.pop(0)

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("remastered.web.true_peak_protection._apply_gain_wav"),
        patch("shutil.copy2"),
    ):
        wav_gain, pw, pa, _ = apply_final_true_peak_protection(
            Path("a"),
            Path("b"),
            ws=WebSettings(
                true_peak_pass_headroom_db=0.0,
                true_peak_max_attempts=6,
                true_peak_refine_max_attempts=0,
            ),
        )
    assert wav_gain < 0
    assert pw <= -1.0
    assert pa <= -1.0
