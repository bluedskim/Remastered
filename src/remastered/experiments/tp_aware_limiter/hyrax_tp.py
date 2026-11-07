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


def _downsample_gain_conservative(gain_os: np.ndarray, os_factor: int, n_out: int) -> np.ndarray:
    usable = (gain_os.size // os_factor) * os_factor
    if usable <= 0:
        return np.ones(n_out, dtype=np.float64)
    block = gain_os[:usable].reshape(-1, os_factor)
    gain = np.min(block, axis=1).astype(np.float64)
    if gain.shape[0] != n_out:
        gain = _trim_rows(gain[:, None], n_out).ravel()
    return gain


def _limit_os_gain_downsample(
    array: np.ndarray,
    config: Config,
    os_factor: int,
) -> tuple[np.ndarray, dict[str, float | int]]:
    n_out = array.shape[0]
    up = signal.resample_poly(array, os_factor, 1, axis=0)
    cfg = _scaled_config(config, os_factor)
    limited_up = hyrax_limit_stock(up, cfg)  # type: ignore[arg-type]

    peak_up = np.max(np.abs(up), axis=1)
    peak_lim = np.max(np.abs(limited_up), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        gain_os = np.where(peak_up > 1e-12, peak_lim / peak_up, 1.0)
    gain_os = np.nan_to_num(gain_os, nan=1.0, posinf=1.0, neginf=1.0)
    gain = _downsample_gain_conservative(gain_os, os_factor, n_out)
    out = array * gain[:, None]

    th = float(config.threshold)
    peak_after = float(np.max(np.abs(out)))
    overshoot_db = 20.0 * np.log10(peak_after / th) if peak_after > th + 1e-9 else 0.0
    stats = {
        "oversample": os_factor,
        "gain_samples_below_unity": int(np.sum(gain < 1.0 - 1e-12)),
        "min_gain": float(np.min(gain)),
        "max_sample_peak_after": peak_after,
        "resampler_overshoot_db": float(overshoot_db),
        "hard_clip_applied": 0,
    }
    return out.astype(array.dtype, copy=False), stats


def limit(
    array: np.ndarray,
    config: Config,
    *,
    oversample: int = DEFAULT_OVERSAMPLE,
) -> np.ndarray:
    """Hyrax with OS peak envelope; conservative gain at base rate (no hard clip)."""
    out, _ = limit_with_stats(array, config, oversample=oversample)
    return out


def limit_with_stats(
    array: np.ndarray,
    config: Config,
    *,
    oversample: int = DEFAULT_OVERSAMPLE,
) -> tuple[np.ndarray, dict[str, float | int]]:
    if oversample <= 1:
        out = hyrax_limit_stock(array, config)
        peak = float(np.max(np.abs(out)))
        th = float(config.threshold)
        return out, {
            "oversample": 1,
            "gain_samples_below_unity": 0,
            "min_gain": 1.0,
            "max_sample_peak_after": peak,
            "resampler_overshoot_db": 20.0 * np.log10(peak / th) if peak > th + 1e-9 else 0.0,
            "hard_clip_applied": 0,
        }
    return _limit_os_gain_downsample(array, config, int(oversample))
