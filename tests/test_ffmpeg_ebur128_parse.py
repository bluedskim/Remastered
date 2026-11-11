from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from remastered.experiments.tp_aware_limiter.ffmpeg_ebur128 import (
    measure_loudness,
    parse_integrated_lufs,
    summary_block,
)


SAMPLE_SUMMARY = """
[Parsed_ebur128_0] Summary:

  Integrated loudness:
    I:         -11.4 LUFS
    Threshold: -21.4 LUFS

  Loudness range:
    LRA:         2.7 LU
    Threshold: -31.4 LUFS

  True peak:
    Peak:       0.7 dBFS
"""


def test_parse_ignores_early_frame_i_lines() -> None:
    noisy = "\n".join(
        [
            "[Parsed_ebur128_0] t: 0.1 TARGET:-23 LUFS I: -70.0 LUFS",
            "[Parsed_ebur128_0] t: 1.0 TARGET:-23 LUFS I: -18.0 LUFS",
            SAMPLE_SUMMARY.strip(),
        ]
    )
    assert parse_integrated_lufs(noisy) == pytest.approx(-11.4)
    assert summary_block(noisy).startswith("Summary:")


def test_parse_returns_none_without_summary() -> None:
    assert parse_integrated_lufs("I: -70.0 LUFS\nno summary") is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_measure_loudness_on_sine_not_minus_70(tmp_path: Path) -> None:
    sr = 44100
    t = np.arange(sr, dtype=np.float32) / sr
    tone = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    wav = tmp_path / "tone.wav"
    sf.write(str(wav), tone, sr, subtype="PCM_16")
    m = measure_loudness(wav)
    assert m["summary_present"] is True
    assert m["lufs_i"] is not None
    assert m["lufs_i"] > -50
    assert m["lufs_i"] < -5
