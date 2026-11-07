# SPDX-License-Identifier: GPL-3.0-or-later
# Derived from matchering 2.0.6 limiter/hyrax.py (Copyright Sergree). See NOTICES.md.

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from scipy import signal

from matchering import Config
from matchering.limiter.hyrax import limit as hyrax_limit_stock

DEFAULT_OVERSAMPLE = 4


def _scaled_config(config: Config, os_factor: int) -> SimpleNamespace:
    """Scale sample rate for limiter timing so attack/release stay in wall-clock ms."""
    return SimpleNamespace(
        internal_sample_rate=int(config.internal_sample_rate * os_factor),
        threshold=config.threshold,
        limiter=config.limiter,
    )


def _trim_rows(audio: np.ndarray, target_rows: int) -> np.ndarray:
    if audio.shape[0] == target_rows:
        return audio
    if audio.shape[0] > target_rows:
        return audio[:target_rows, :]
    pad = target_rows - audio.shape[0]
    return np.pad(audio, ((0, pad), (0, 0)), mode="constant")


def limit(
    array: np.ndarray,
    config: Config,
    *,
    oversample: int = DEFAULT_OVERSAMPLE,
) -> np.ndarray:
    """Hyrax limiter with oversampled peak detection (intersample-aware)."""
    if oversample <= 1:
        return hyrax_limit_stock(array, config)

    os_factor = int(oversample)
    up = signal.resample_poly(array, os_factor, 1, axis=0)
    cfg = _scaled_config(config, os_factor)
    limited_up = hyrax_limit_stock(up, cfg)  # type: ignore[arg-type]
    down = signal.resample_poly(limited_up, 1, os_factor, axis=0)
    down = _trim_rows(down, array.shape[0])
    # Resampler stopband can reintroduce small >threshold samples; keep brickwall intent.
    th = float(config.threshold)
    return np.clip(down, -th, th)
