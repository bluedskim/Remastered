from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

from remastered.master import run_matchering
from remastered.stereo_width import (
    DEFAULT_STEREO_WIDTH_MODE,
    preserve_stereo_width,
    stereo_lr_correlation,
    stereo_side_mid_ratio_db,
    stereo_width_label,
)


def test_default_mode_and_labels() -> None:
    assert DEFAULT_STEREO_WIDTH_MODE == "follow_reference"
    assert "기본" in stereo_width_label("follow_reference")
    assert "기본" not in stereo_width_label("preserve")
    assert stereo_width_label(None) == "레퍼런스 따라가기 (기본)"


def _write_wide_stereo_wav(path: Path, duration: float = 4.0, sr: int = 44100) -> None:
    """Wide, imbalanced stereo (like hard-panned material with level difference L/R)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(sr * duration)
    t = np.arange(n, dtype=np.float64) / sr
    left = 0.36 * np.sin(2 * math.pi * 440.0 * t)
    right = 0.09 * np.sin(2 * math.pi * 520.0 * t)
    stereo = np.column_stack([left, right]).astype(np.float32)
    sf.write(path, stereo, sr, subtype="PCM_16")


def _write_narrow_reference_wav(path: Path, freq: float = 440.0, duration: float = 4.0, sr: int = 44100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(sr * duration)
    t = np.arange(n, dtype=np.float64) / sr
    tone = 0.35 * np.sin(2 * math.pi * freq * t)
    mono = tone
    stereo = np.column_stack([mono, mono]).astype(np.float32)
    sf.write(path, stereo, sr, subtype="PCM_16")


def _read_stereo_wav(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(path, always_2d=True)
    return data.astype(np.float64), int(sr)


def test_preserve_restores_side_mid_ratio(tmp_path: Path) -> None:
    target = tmp_path / "target.wav"
    reference = tmp_path / "reference.wav"
    _write_wide_stereo_wav(target)
    _write_narrow_reference_wav(reference)

    orig, _ = _read_stereo_wav(target)
    sm_orig = stereo_side_mid_ratio_db(orig)
    assert sm_orig > -15.0

    out_follow = tmp_path / "follow"
    result_follow = run_matchering(
        target,
        reference,
        out_follow,
        bit_depth=16,
        stereo_width="follow_reference",
    )
    mastered_follow, _ = _read_stereo_wav(result_follow.output_path)
    sm_follow = stereo_side_mid_ratio_db(mastered_follow)

    out_preserve = tmp_path / "preserve"
    result_preserve = run_matchering(
        target,
        reference,
        out_preserve,
        bit_depth=16,
        stereo_width="preserve",
    )
    mastered_preserve, _ = _read_stereo_wav(result_preserve.output_path)
    sm_preserve = stereo_side_mid_ratio_db(mastered_preserve)

    ref_sm, _ = _read_stereo_wav(reference)
    sm_ref = stereo_side_mid_ratio_db(ref_sm)
    assert sm_follow < sm_orig - 2.0, "follow_reference should narrow toward reference"
    assert abs(sm_preserve - sm_orig) < 1.0, (
        f"preserve should stay within 1 dB of original S/M (orig={sm_orig:.2f}, out={sm_preserve:.2f})"
    )
    assert abs(sm_follow - sm_ref) < abs(sm_preserve - sm_ref)


def test_no_clipping_16bit(tmp_path: Path) -> None:
    target = tmp_path / "target.wav"
    reference = tmp_path / "reference.wav"
    _write_wide_stereo_wav(target, duration=3.0)
    _write_narrow_reference_wav(reference, duration=3.0)
    result = run_matchering(
        target,
        reference,
        tmp_path / "out",
        bit_depth=16,
        stereo_width="preserve",
    )
    peak = 0.0
    with wave.open(str(result.output_path), "rb") as wf:
        assert wf.getsampwidth() == 2
        frames = wf.readframes(wf.getnframes())
        for i in range(0, len(frames), 2):
            sample = struct.unpack("<h", frames[i : i + 2])[0]
            peak = max(peak, abs(sample))
    assert peak <= 32767


def test_mono_near_mono_is_noop() -> None:
    sr = 44100
    n = sr * 2
    mono = np.ones((n, 2), dtype=np.float64) * 0.01
    mastered = mono.copy()
    out = preserve_stereo_width(mono, mastered, sr)
    assert np.allclose(out, mastered)


def test_preserve_keeps_lower_lr_correlation_than_follow(tmp_path: Path) -> None:
    target = tmp_path / "target.wav"
    reference = tmp_path / "reference.wav"
    _write_wide_stereo_wav(target)
    _write_narrow_reference_wav(reference)

    preserve_out, _ = _read_stereo_wav(
        run_matchering(target, reference, tmp_path / "out", stereo_width="preserve").output_path
    )
    follow_out, _ = _read_stereo_wav(
        run_matchering(target, reference, tmp_path / "f", stereo_width="follow_reference").output_path
    )
    assert stereo_lr_correlation(preserve_out) < stereo_lr_correlation(follow_out)
