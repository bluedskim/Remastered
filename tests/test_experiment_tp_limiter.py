from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scipy")

from matchering import Config
from matchering.limiter.hyrax import limit as stock_limit
from remastered.experiments.tp_aware_limiter.hyrax_tp import limit as tp_limit


def _hot_stereo(n: int = 44100, sr: int = 44100) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / sr
    left = 1.8 * np.sin(2 * np.pi * 997 * t)
    right = 1.8 * np.sin(2 * np.pi * 1303 * t)
    return np.stack([left, right], axis=1).astype(np.float32)


def test_tp_limiter_preserves_length_and_no_nan() -> None:
    audio = _hot_stereo()
    cfg = Config()
    out = tp_limit(audio, cfg, oversample=4)
    assert out.shape == audio.shape
    assert not np.any(np.isnan(out))
    assert not np.any(np.isinf(out))


def test_tp_limiter_caps_near_threshold() -> None:
    audio = _hot_stereo()
    cfg = Config()
    exp = tp_limit(audio, cfg, oversample=4)
    peak = float(np.max(np.abs(exp)))
    assert peak <= float(cfg.threshold) + 0.002
    assert peak <= float(np.max(np.abs(audio)))


def test_tp_limiter_silence_unchanged() -> None:
    audio = np.zeros((8000, 2), dtype=np.float32)
    cfg = Config()
    out = tp_limit(audio, cfg)
    assert np.max(np.abs(out)) < 1e-12


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_harness_metrics_on_synthetic(tmp_path: Path) -> None:
    from remastered.experiments.tp_aware_limiter.metrics import measure_wav_file
    import soundfile as sf

    wav = tmp_path / "hot.wav"
    sf.write(str(wav), _hot_stereo(22050), 44100, subtype="PCM_16")
    m = measure_wav_file(wav)
    assert m["dbtp"] is not None
    assert m["nan"] == 0
