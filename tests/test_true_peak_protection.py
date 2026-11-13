from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from remastered.web.settings import WebSettings
from remastered.web.true_peak_protection import (
    apply_final_true_peak_protection,
    copy_matchering_to_raw,
    pass_ceiling_dbtp,
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


def test_pass_ceiling_subtracts_margin_below_target() -> None:
    ws = WebSettings(true_peak_ceiling_dbtp=-1.0, true_peak_pass_headroom_db=0.05)
    assert pass_ceiling_dbtp(ws) == pytest.approx(-1.05)


def test_apply_tp_passes_first_attempt_when_both_under_pass_ceiling() -> None:
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
        g, peak_wav, peak_aac, _ = apply_final_true_peak_protection(
            wav_in,
            wav_out,
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.05,
                true_peak_max_attempts=5,
                true_peak_min_step_db=0.05,
                true_peak_refine_max_attempts=0,
            ),
        )
    assert g == 0.0
    assert peak_wav == -2.0
    assert peak_aac == -2.0
    ag.assert_not_called()
    cp.assert_called()


def test_apply_tp_rejects_aac_minus_0_7_at_minus_1_ceiling() -> None:
    """AAC at -0.7 must not pass when ceiling is -1.0 (old +0.5 headroom bug)."""
    wav_in = Path("in.wav")
    wav_out = Path("out.wav")
    # raw, guarded WAV, preview fail, preview pass
    peaks = [-1.2, -1.2, -0.7, -2.5]

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
        g, pw, pa, preview_extra = apply_final_true_peak_protection(
            wav_in,
            wav_out,
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.0,
                true_peak_max_attempts=8,
                true_peak_min_step_db=0.05,
                true_peak_refine_max_attempts=0,
            ),
        )
    assert g == 0.0
    assert preview_extra < 0
    assert pw <= -1.0
    assert pa <= -1.0


def test_apply_tp_rejects_aac_minus_0_6_without_legacy_tolerance() -> None:
    peaks = [-1.1, -1.1, -0.6, -2.0, -1.05]

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
        _, pw, pa, _ = apply_final_true_peak_protection(
            Path("in.wav"),
            Path("out.wav"),
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.0,
                true_peak_max_attempts=10,
                true_peak_min_step_db=0.05,
                true_peak_refine_max_attempts=0,
            ),
        )
    assert pw <= -1.0
    assert pa <= -1.0


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
        g, _, _, _ = apply_final_true_peak_protection(
            wav_in,
            wav_out,
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.05,
                true_peak_max_attempts=3,
                true_peak_min_step_db=0.05,
                true_peak_refine_max_attempts=0,
            ),
        )
    assert g == 0.0
    assert wav_out.read_bytes() == b"x"


def test_apply_tp_raises_after_max_attempts_and_restores_output(tmp_path: Path) -> None:
    wav_in = tmp_path / "in.wav"
    wav_out = tmp_path / "out.wav"
    wav_in.write_bytes(b"RAW-MASTER")
    wav_out.write_bytes(b"RAW-MASTER")

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
                    true_peak_pass_headroom_db=0.0,
                    true_peak_max_attempts=3,
                    true_peak_min_step_db=0.05,
                    true_peak_refine_max_attempts=0,
                ),
            )
    assert wav_out.read_bytes() == b"RAW-MASTER"


def test_apply_tp_extra_attenuation_when_aac_overshoots(tmp_path: Path) -> None:
    wav_in = tmp_path / "in.wav"
    wav_out = tmp_path / "out.wav"
    wav_in.write_bytes(b"y")

    peaks = [-1.2, -1.2, 0.5, -2.5]

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
        wav_gain, _, _, preview_extra = apply_final_true_peak_protection(
            wav_in,
            wav_out,
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.0,
                true_peak_max_attempts=8,
                true_peak_min_step_db=0.05,
                true_peak_refine_max_attempts=0,
            ),
        )
    assert wav_gain == 0.0
    assert preview_extra < 0
    assert preview_extra < wav_gain
    assert ag.called


def test_apply_tp_wav_gain_less_than_preview_extra_when_only_aac_overshoots() -> None:
    """Saved WAV uses minimal gain; preview carries extra attenuation for AAC."""
    peaks = [-0.5, -1.1, 0.4, -1.2]

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
        wav_gain, _, _, preview_extra = apply_final_true_peak_protection(
            Path("in.wav"),
            Path("out.wav"),
            ws=WebSettings(
                true_peak_ceiling_dbtp=-1.0,
                true_peak_pass_headroom_db=0.05,
                true_peak_max_attempts=6,
                true_peak_min_step_db=0.05,
                true_peak_refine_max_attempts=0,
            ),
        )
    assert wav_gain < 0
    assert preview_extra < 0
    assert abs(preview_extra) > abs(wav_gain)


def test_measure_none_raises(tmp_path: Path) -> None:
    wav_in = tmp_path / "in.wav"
    wav_out = tmp_path / "out.wav"
    wav_in.write_bytes(b"raw")
    wav_out.write_bytes(b"raw")

    with patch(
        "remastered.web.true_peak_protection._measure_true_peak_dbtp",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="Could not measure"):
            apply_final_true_peak_protection(wav_in, wav_out)
