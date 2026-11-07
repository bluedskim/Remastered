from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from remastered.web.settings import WebSettings
from remastered.web.true_peak_protection import (
    apply_final_true_peak_protection,
    copy_matchering_to_raw,
    raw_mastered_path_for_final,
)


def test_raw_master_path_convention() -> None:
    p = Path("/work/vid/master/mastered_16bit.wav")
    assert raw_mastered_path_for_final(p) == Path(
        "/work/vid/master/mastered_matchering_16bit.wav"
    )


def test_copy_matchering_preserves_unattenuated(tmp_path: Path) -> None:
    final = tmp_path / "mastered_16bit.wav"
    final.write_bytes(b"MASTERED-UNIQUE")
    raw = copy_matchering_to_raw(final)
    assert raw.name == "mastered_matchering_16bit.wav"
    assert raw.read_bytes() == b"MASTERED-UNIQUE"


def test_apply_tp_passes_first_attempt_when_wav_and_aac_safe() -> None:
    wav_in = Path("in.wav")
    wav_out = Path("out.wav")

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            return_value=-2.0,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("remastered.web.true_peak_protection._apply_gain_wav") as ag,
        patch("shutil.copy2") as cp,
    ):
        g, peak = apply_final_true_peak_protection(
            wav_in,
            wav_out,
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.5,
                true_peak_max_attempts=5,
                true_peak_min_step_db=0.05,
            ),
        )
    assert g == 0.0
    assert peak == -2.0
    ag.assert_not_called()
    cp.assert_called_once()


def test_apply_tp_no_boost_when_raw_already_under_ceiling(tmp_path: Path) -> None:
    wav_in = tmp_path / "in.wav"
    wav_out = tmp_path / "out.wav"
    wav_in.write_bytes(b"x")

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            return_value=-1.5,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
    ):
        g, _ = apply_final_true_peak_protection(
            wav_in,
            wav_out,
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.5,
                true_peak_max_attempts=3,
                true_peak_min_step_db=0.05,
            ),
        )
    assert g == 0.0
    assert wav_out.read_bytes() == b"x"


def test_apply_tp_raises_after_max_attempts() -> None:
    wav_in = Path("in.wav")
    wav_out = Path("out.wav")

    def fake_measure(path: Path, **kwargs):
        if path.suffix.lower() == ".m4a":
            return 5.0
        return 2.0

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("remastered.web.true_peak_protection._apply_gain_wav"),
    ):
        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            apply_final_true_peak_protection(
                wav_in,
                wav_out,
                ws=WebSettings(
                    true_peak_ceiling_dbtp=-1.0,
                    true_peak_pass_headroom_db=0.5,
                    true_peak_max_attempts=3,
                    true_peak_min_step_db=0.05,
                ),
            )


def test_apply_tp_extra_attenuation_when_aac_overshoots(tmp_path: Path) -> None:
    wav_in = tmp_path / "in.wav"
    wav_out = tmp_path / "out.wav"
    wav_in.write_bytes(b"y")

    # Order: raw WAV peak; attempt 1: mastered WAV + AAC; attempt 2: WAV + AAC.
    peaks = [-1.2, -1.2, 0.5, -2.5, -2.5]

    def fake_measure(path: Path, **kwargs):
        return peaks.pop(0)

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("remastered.web.true_peak_protection._apply_gain_wav") as ag,
    ):
        g, _ = apply_final_true_peak_protection(
            wav_in,
            wav_out,
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.5,
                true_peak_max_attempts=8,
                true_peak_min_step_db=0.05,
            ),
        )
    assert g < 0
    assert ag.called


def test_measure_none_raises() -> None:
    wav_in = Path("in.wav")
    wav_out = Path("out.wav")

    with patch(
        "remastered.web.true_peak_protection._measure_true_peak_dbtp",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="Could not measure"):
            apply_final_true_peak_protection(wav_in, wav_out)
