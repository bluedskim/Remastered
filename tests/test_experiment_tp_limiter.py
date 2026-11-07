from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")

from matchering import Config
from matchering.limiter.hyrax import limit as stock_limit
from remastered.experiments.tp_aware_limiter.hyrax_tp import limit as tp_limit
from remastered.experiments.tp_aware_limiter.hyrax_tp import limit_with_stats


def _hot_stereo(n: int = 44100, sr: int = 44100) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / sr
    left = 1.8 * np.sin(2 * np.pi * 997 * t)
    right = 1.8 * np.sin(2 * np.pi * 1303 * t)
    return np.stack([left, right], axis=1).astype(np.float32)


def _near_nyquist(n: int = 8192, sr: int = 44100) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / sr
    mono = 1.2 * np.sin(2 * np.pi * (sr * 0.45) * t)
    return np.stack([mono, mono], axis=1).astype(np.float32)


def test_tp_limiter_preserves_length_and_no_nan() -> None:
    audio = _hot_stereo()
    cfg = Config()
    out = tp_limit(audio, cfg, oversample=4)
    assert out.shape == audio.shape
    assert not np.any(np.isnan(out))
    assert not np.any(np.isinf(out))


def test_tp_limiter_no_hard_clip_flag() -> None:
    audio = _hot_stereo(22050)
    cfg = Config()
    _, st = limit_with_stats(audio, cfg, oversample=4)
    assert st["hard_clip_applied"] == 0


def test_tp_limiter_caps_near_threshold() -> None:
    audio = _hot_stereo()
    cfg = Config()
    out = tp_limit(audio, cfg, oversample=4)
    peak = float(np.max(np.abs(out)))
    assert peak <= float(cfg.threshold) + 0.02


def test_tp_limiter_silence_unchanged() -> None:
    audio = np.zeros((8000, 2), dtype=np.float32)
    cfg = Config()
    out = tp_limit(audio, cfg)
    assert np.max(np.abs(out)) < 1e-12


def test_tp_limiter_short_buffer() -> None:
    audio = _hot_stereo(n=512)
    cfg = Config()
    out = tp_limit(audio, cfg, oversample=4)
    assert out.shape == audio.shape


def test_tp_limiter_near_nyquist_alignment() -> None:
    audio = _near_nyquist()
    cfg = Config()
    out = tp_limit(audio, cfg, oversample=4)
    assert out.shape == audio.shape
    assert float(np.max(np.abs(out))) <= float(cfg.threshold) + 0.05


def test_tp_limiter_stereo_linked_gain() -> None:
    audio = _hot_stereo(4096)
    cfg = Config()
    out = tp_limit(audio, cfg, oversample=4)
    # Both channels should be attenuated together (no single channel at unity while other crushed)
    l_peak = float(np.max(np.abs(out[:, 0])))
    r_peak = float(np.max(np.abs(out[:, 1])))
    assert abs(l_peak - r_peak) < 0.15


def test_isp_overshoot_may_drop_with_constant_gain_only() -> None:
    """Attenuation alone can lower measured TP without a limiter (not a listening claim)."""
    sr = 44100
    n = sr // 4
    t = np.arange(n, dtype=np.float64) / sr
    x = (0.95 * np.sin(2 * np.pi * 12000 * t)).astype(np.float32)
    audio = np.stack([x, x], axis=1)
    scaled = audio * 10 ** (-3.0 / 20.0)
    assert float(np.max(np.abs(scaled))) < float(np.max(np.abs(audio)))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_harness_metrics_on_synthetic(tmp_path: Path) -> None:
    from remastered.experiments.tp_aware_limiter.metrics import measure_wav_file
    import soundfile as sf

    wav = tmp_path / "hot.wav"
    sf.write(str(wav), _hot_stereo(22050), 44100, subtype="PCM_16")
    m = measure_wav_file(wav)
    assert m["summary_present"] is True
    assert m["lufs_i"] is not None and m["lufs_i"] > -60
    assert m["dbtp"] is not None
    assert m["nan"] == 0
