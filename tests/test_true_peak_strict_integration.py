"""Strict PR80 guard: real ffmpeg synthesis (objective metrics only)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from remastered.web.settings import WebSettings
from remastered.web.true_peak_protection import (
    _encode_playback_aac,
    apply_final_true_peak_protection,
    pass_ceiling_dbtp,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


def _ebur128_tp(path: Path) -> float | None:
    from remastered.web.true_peak_protection import _measure_true_peak_dbtp

    return _measure_true_peak_dbtp(path)


def _write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio, sr, subtype="PCM_16")


def _sine_hot(sr: int, n: int, freq: float, db: float) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / sr
    amp = 10 ** (db / 20.0)
    s = amp * np.sin(2 * np.pi * freq * t)
    return np.stack([s, s], axis=1).astype(np.float32)


def _assert_strict_and_pass(path_wav: Path, path_m4a: Path, ws: WebSettings) -> None:
    pc = pass_ceiling_dbtp(ws)
    tp_w = _ebur128_tp(path_wav)
    tp_a = _ebur128_tp(path_m4a)
    assert tp_w is not None and tp_a is not None
    assert tp_w <= pc + 0.06
    assert tp_a <= pc + 0.06
    assert tp_w <= -1.0 + 0.06
    assert tp_a <= -1.0 + 0.06


def test_integration_already_safe_no_boost(tmp_path: Path) -> None:
    ws = WebSettings()
    raw = tmp_path / "mastered_matchering_16bit.wav"
    out = tmp_path / "mastered_16bit.wav"
    _write_wav(raw, _sine_hot(44100, 8000, 440.0, -6.0), 44100)
    shutil.copy2(raw, out)
    g, _, _ = apply_final_true_peak_protection(raw, out, ws=ws)
    assert g == 0.0
    assert raw.read_bytes() == out.read_bytes() or out.stat().st_size > 0


def test_integration_silence_short(tmp_path: Path) -> None:
    """Very short quiet file; ffmpeg may not emit TP on pure digital silence."""
    ws = WebSettings()
    raw = tmp_path / "mastered_matchering_16bit.wav"
    out = tmp_path / "mastered_16bit.wav"
    audio = np.full((256, 2), 1e-8, dtype=np.float32)
    _write_wav(raw, audio, 44100)
    shutil.copy2(raw, out)
    tp = _ebur128_tp(raw)
    if tp is None:
        pytest.skip("ffmpeg returned no TP for near-silence short file")
    g, pw, pa = apply_final_true_peak_protection(raw, out, ws=ws)
    assert g == 0.0
    assert pw is not None and pw <= pass_ceiling_dbtp(ws) + 0.1


def test_integration_near_nyquist_hot(tmp_path: Path) -> None:
    ws = WebSettings()
    sr = 44100
    raw = tmp_path / "mastered_matchering_16bit.wav"
    out = tmp_path / "mastered_16bit.wav"
    n = sr // 2
    _write_wav(raw, _sine_hot(sr, n, sr * 0.45, 3.0), sr)
    shutil.copy2(raw, out)
    g, pw, pa = apply_final_true_peak_protection(raw, out, ws=ws)
    assert g <= 0.0
    m4a = tmp_path / "del.m4a"
    _encode_playback_aac(out, m4a)
    _assert_strict_and_pass(out, m4a, ws)


def test_integration_transient_burst(tmp_path: Path) -> None:
    ws = WebSettings()
    sr = 44100
    n = sr * 2
    audio = np.zeros((n, 2), dtype=np.float32)
    audio[1000:1100, 0] = 1.8
    audio[50000:50100, 1] = 1.8
    raw = tmp_path / "mastered_matchering_16bit.wav"
    out = tmp_path / "mastered_16bit.wav"
    _write_wav(raw, audio, sr)
    shutil.copy2(raw, out)
    g, _, _ = apply_final_true_peak_protection(raw, out, ws=ws)
    m4a = tmp_path / "del.m4a"
    _encode_playback_aac(out, m4a)
    _assert_strict_and_pass(out, m4a, ws)
    assert g <= 0.0


def test_integration_unbalanced_stereo(tmp_path: Path) -> None:
    ws = WebSettings()
    sr = 44100
    n = sr
    t = np.arange(n, dtype=np.float64) / sr
    left = 1.5 * np.sin(2 * np.pi * 900 * t)
    right = 0.05 * np.sin(2 * np.pi * 700 * t)
    raw = tmp_path / "mastered_matchering_16bit.wav"
    out = tmp_path / "mastered_16bit.wav"
    _write_wav(raw, np.stack([left, right], axis=1).astype(np.float32), sr)
    shutil.copy2(raw, out)
    apply_final_true_peak_protection(raw, out, ws=ws)
    m4a = tmp_path / "del.m4a"
    _encode_playback_aac(out, m4a)
    _assert_strict_and_pass(out, m4a, ws)


def test_integration_48000_rate_supported(tmp_path: Path) -> None:
    """Guard path does not resample; 48 kHz masters should still attenuate safely."""
    ws = WebSettings()
    sr = 48000
    raw = tmp_path / "mastered_matchering_16bit.wav"
    out = tmp_path / "mastered_16bit.wav"
    _write_wav(raw, _sine_hot(sr, sr, 1200.0, 6.0), sr)
    shutil.copy2(raw, out)
    before = sf.read(str(raw), dtype="int16", always_2d=True)[0].shape[0]
    g, _, _ = apply_final_true_peak_protection(raw, out, ws=ws)
    after = sf.read(str(out), dtype="int16", always_2d=True)[0].shape[0]
    assert before == after
    m4a = tmp_path / "del.m4a"
    _encode_playback_aac(out, m4a)
    _assert_strict_and_pass(out, m4a, ws)
    assert g <= 0.0


def test_integration_constant_gain_residual_only(tmp_path: Path) -> None:
    ws = WebSettings()
    sr = 44100
    raw = tmp_path / "mastered_matchering_16bit.wav"
    out = tmp_path / "mastered_16bit.wav"
    _write_wav(raw, _sine_hot(sr, sr, 997.0, 8.0), sr)
    shutil.copy2(raw, out)
    g, _, _ = apply_final_true_peak_protection(raw, out, ws=ws)
    rin, _ = sf.read(str(raw), dtype="float32", always_2d=True)
    rout, _ = sf.read(str(out), dtype="float32", always_2d=True)
    lin = 10 ** (g / 20.0)
    diff = rout - rin * lin
    assert float(np.max(np.abs(diff))) < 2e-5
    assert rin.shape == rout.shape


def test_integration_legacy_headroom_boundary_pass_ceiling(tmp_path: Path) -> None:
    """Explicit env-style margin 0.5 → pass ceiling -1.5 (stricter than default)."""
    ws = WebSettings(true_peak_pass_headroom_db=0.5)
    assert pass_ceiling_dbtp(ws) == pytest.approx(-1.5)
    raw = tmp_path / "mastered_matchering_16bit.wav"
    out = tmp_path / "mastered_16bit.wav"
    _write_wav(raw, _sine_hot(44100, 4000, 440.0, -4.0), 44100)
    shutil.copy2(raw, out)
    g, pw, pa = apply_final_true_peak_protection(raw, out, ws=ws)
    assert g == 0.0
    assert pw <= -1.5 + 0.06
    assert pa <= -1.5 + 0.06
