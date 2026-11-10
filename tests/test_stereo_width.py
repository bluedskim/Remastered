from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import numpy as np
import soundfile as sf
from matchering.dsp import lr_to_ms, ms_to_lr

from remastered.master import run_matchering
from remastered.stereo_width import (
    DEFAULT_STEREO_WIDTH_MODE,
    _apply_stereo_width_donor,
    follow_reference_stereo_width,
    preserve_stereo_width,
    stereo_side_mid_ratio_db,
    stereo_width_label,
)


def test_default_mode_and_labels() -> None:
    assert DEFAULT_STEREO_WIDTH_MODE == "follow_reference"
    assert "default" in stereo_width_label("follow_reference").lower()
    assert "reference" in stereo_width_label("follow_reference").lower()
    assert "default" not in stereo_width_label("preserve").lower()
    assert stereo_width_label(None) == "Open toward reference width (default)"


def _write_wide_stereo_wav(path: Path, duration: float = 4.0, sr: int = 44100) -> None:
    """Wide stereo with strong, measurable side energy (mid/side tones)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stereo = _synthetic_stereo(sr, duration, side_amp=0.28).astype(np.float32)
    sf.write(path, stereo, sr, subtype="PCM_16")


def _write_narrow_stereo_wav(path: Path, freq: float = 440.0, duration: float = 4.0, sr: int = 44100) -> None:
    """Nearly mono source (narrow S/M)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(sr * duration)
    t = np.arange(n, dtype=np.float64) / sr
    tone = 0.35 * np.sin(2 * math.pi * freq * t)
    left = tone
    right = tone * 0.92
    stereo = np.column_stack([left, right]).astype(np.float32)
    sf.write(path, stereo, sr, subtype="PCM_16")


def _write_reference_stereo_wav(
    path: Path,
    *,
    side_scale: float = 0.55,
    freq: float = 440.0,
    duration: float = 4.0,
    sr: int = 44100,
) -> None:
    """Stereo reference with controllable width via mid/side balance."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(sr * duration)
    t = np.arange(n, dtype=np.float64) / sr
    mid = 0.35 * np.sin(2 * math.pi * freq * t)
    side = 0.35 * side_scale * np.sin(2 * math.pi * (freq * 1.02) * t)
    stereo = ms_to_lr(mid, side).astype(np.float32)
    sf.write(path, stereo, sr, subtype="PCM_16")


def _read_stereo_wav(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(path, always_2d=True)
    return data.astype(np.float64), int(sr)


def test_follow_reference_skips_narrowing_when_source_already_wider(tmp_path: Path) -> None:
    target = tmp_path / "target.wav"
    reference = tmp_path / "reference.wav"
    _write_wide_stereo_wav(target)
    _write_reference_stereo_wav(reference, side_scale=0.15)

    orig, _ = _read_stereo_wav(target)
    sm_orig = stereo_side_mid_ratio_db(orig)
    ref, _ = _read_stereo_wav(reference)
    sm_ref = stereo_side_mid_ratio_db(ref)
    assert sm_orig > sm_ref + 1.0, "fixture: source should be wider than reference (S/M)"

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

    assert sm_follow >= sm_orig - 1.0, (
        "follow_reference must not narrow an already-wider source "
        f"(orig={sm_orig:.2f}, out={sm_follow:.2f}, ref={sm_ref:.2f} dB S/M)"
    )


def test_follow_reference_widens_narrow_source_toward_reference(tmp_path: Path) -> None:
    target = tmp_path / "target.wav"
    reference = tmp_path / "reference.wav"
    _write_narrow_stereo_wav(target)
    _write_reference_stereo_wav(reference, side_scale=0.6)

    orig, _ = _read_stereo_wav(target)
    sm_orig = stereo_side_mid_ratio_db(orig)
    ref, _ = _read_stereo_wav(reference)
    sm_ref = stereo_side_mid_ratio_db(ref)
    assert sm_orig < sm_ref - 2.0, "fixture: source should be narrower than reference"

    result = run_matchering(
        target,
        reference,
        tmp_path / "out",
        bit_depth=16,
        stereo_width="follow_reference",
    )
    mastered, _ = _read_stereo_wav(result.output_path)
    sm_out = stereo_side_mid_ratio_db(mastered)

    assert sm_out > sm_orig + 2.0, "narrow source should be widened"
    assert abs(sm_out - sm_ref) < abs(sm_orig - sm_ref)


def test_preserve_restores_side_mid_ratio() -> None:
    sr = 44100
    source = _synthetic_stereo(sr, 4.0, side_amp=0.28)
    mastered = _synthetic_stereo(sr, 4.0, side_amp=0.06)
    sm_orig = stereo_side_mid_ratio_db(source)
    sm_pre = stereo_side_mid_ratio_db(mastered)
    assert sm_pre < sm_orig - 2.0

    out = preserve_stereo_width(source, mastered, sr)
    sm_out = stereo_side_mid_ratio_db(out)
    assert abs(sm_out - sm_orig) < 1.5, (
        f"preserve should restore source S/M (orig={sm_orig:.2f}, out={sm_out:.2f})"
    )


def _synthetic_stereo(
    sr: int,
    duration: float,
    side_amp: float,
    *,
    freq: float = 440.0,
) -> np.ndarray:
    n = int(sr * duration)
    t = np.arange(n, dtype=np.float64) / sr
    mid = 0.4 * np.sin(2 * math.pi * freq * t)
    side = side_amp * np.sin(2 * math.pi * (freq * 1.01) * t)
    return ms_to_lr(mid, side)


def test_follow_reference_unit_preserves_width_when_source_wider() -> None:
    sr = 44100
    source = _synthetic_stereo(sr, 3.0, side_amp=0.35)
    reference = _synthetic_stereo(sr, 3.0, side_amp=0.08)
    mastered = _synthetic_stereo(sr, 3.0, side_amp=0.05)
    src_sm = stereo_side_mid_ratio_db(source)
    ref_sm = stereo_side_mid_ratio_db(reference)
    mst_sm = stereo_side_mid_ratio_db(mastered)
    assert src_sm > ref_sm
    assert mst_sm < src_sm - 0.5, "fixture: matchering-like narrowing"

    out, decision = follow_reference_stereo_width(source, reference, mastered, sr)
    assert decision.applied
    out_sm = stereo_side_mid_ratio_db(out)
    assert out_sm > mst_sm + 0.5
    assert abs(out_sm - src_sm) < abs(out_sm - ref_sm)


def test_follow_reference_unit_caps_overshoot_at_reference() -> None:
    sr = 44100
    source = _synthetic_stereo(sr, 4.0, side_amp=0.02)
    reference = _synthetic_stereo(sr, 4.0, side_amp=0.2)
    mastered = _synthetic_stereo(sr, 4.0, side_amp=0.45)
    ref_sm = stereo_side_mid_ratio_db(reference)
    mst_sm = stereo_side_mid_ratio_db(mastered)
    assert stereo_side_mid_ratio_db(source) < ref_sm
    assert mst_sm > ref_sm + 0.5, "fixture: mastered overshoots reference width"

    out, decision = follow_reference_stereo_width(source, reference, mastered, sr)
    assert decision.applied
    out_sm = stereo_side_mid_ratio_db(out)
    assert out_sm <= ref_sm + 0.75
    assert abs(out_sm - ref_sm) < abs(mst_sm - ref_sm)


def test_follow_reference_unit_widens_narrow_mastered() -> None:
    sr = 44100
    source = _synthetic_stereo(sr, 4.0, side_amp=0.03)
    reference = _synthetic_stereo(sr, 4.0, side_amp=0.22)
    mastered = _synthetic_stereo(sr, 4.0, side_amp=0.04)
    ref_sm = stereo_side_mid_ratio_db(reference)
    src_sm = stereo_side_mid_ratio_db(source)
    mst_sm = stereo_side_mid_ratio_db(mastered)
    assert src_sm < ref_sm and mst_sm < ref_sm - 1.0

    out, decision = follow_reference_stereo_width(source, reference, mastered, sr)
    assert decision.applied
    out_sm = stereo_side_mid_ratio_db(out)
    assert out_sm > mst_sm + 1.0
    assert abs(out_sm - ref_sm) < 3.5


def test_no_clipping_16bit(tmp_path: Path) -> None:
    target = tmp_path / "target.wav"
    reference = tmp_path / "reference.wav"
    _write_wide_stereo_wav(target, duration=3.0)
    _write_reference_stereo_wav(reference, duration=3.0)
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


def test_apply_donor_widens_mastered_toward_donor() -> None:
    sr = 44100
    source = _synthetic_stereo(sr, 3.0, side_amp=0.2)
    reference = _synthetic_stereo(sr, 3.0, side_amp=0.25)
    mastered = _synthetic_stereo(sr, 3.0, side_amp=0.05)
    mst_sm = stereo_side_mid_ratio_db(mastered)
    out = _apply_stereo_width_donor(reference, mastered, sr)
    assert stereo_side_mid_ratio_db(out) > mst_sm + 1.0


def test_preserve_and_follow_wide_source_match_width(tmp_path: Path) -> None:
    target = tmp_path / "target.wav"
    reference = tmp_path / "reference.wav"
    _write_wide_stereo_wav(target)
    _write_reference_stereo_wav(reference, side_scale=0.15)

    preserve_out, _ = _read_stereo_wav(
        run_matchering(target, reference, tmp_path / "out", stereo_width="preserve").output_path
    )
    follow_out, _ = _read_stereo_wav(
        run_matchering(target, reference, tmp_path / "f", stereo_width="follow_reference").output_path
    )
    sm_p = stereo_side_mid_ratio_db(preserve_out)
    sm_f = stereo_side_mid_ratio_db(follow_out)
    assert abs(sm_p - sm_f) < 1.5, "wide source: preserve and follow should both keep source width"
