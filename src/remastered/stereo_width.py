from __future__ import annotations

from typing import Literal

import numpy as np
from matchering.dsp import lr_to_ms, ms_to_lr
from scipy import signal

StereoWidthMode = Literal["preserve", "follow_reference"]

DEFAULT_STEREO_WIDTH_MODE: StereoWidthMode = "follow_reference"
VALID_STEREO_WIDTH_MODES = frozenset({"preserve", "follow_reference"})

_MONO_SIDE_MID_THRESHOLD_DB = -30.0
_GAIN_CLAMP_DB = 12.0
_N_BANDS = 10


def stereo_side_mid_ratio_db(stereo: np.ndarray) -> float:
    """Side/Mid RMS level ratio in dB (20·log10 RMS_side / RMS_mid)."""
    stereo = np.asarray(stereo, dtype=np.float64)
    if stereo.ndim != 2 or stereo.shape[1] != 2:
        return float("-inf")
    mid, side = lr_to_ms(stereo)
    mid_rms = float(np.sqrt(np.mean(mid**2)))
    side_rms = float(np.sqrt(np.mean(side**2)))
    mid_rms = max(mid_rms, 1e-12)
    side_rms = max(side_rms, 1e-12)
    return 20.0 * np.log10(side_rms / mid_rms)


def stereo_lr_correlation(stereo: np.ndarray) -> float:
    stereo = np.asarray(stereo, dtype=np.float64)
    if stereo.ndim != 2 or stereo.shape[1] != 2 or len(stereo) < 2:
        return 1.0
    left = stereo[:, 0]
    right = stereo[:, 1]
    if np.std(left) < 1e-12 or np.std(right) < 1e-12:
        return 1.0
    return float(np.corrcoef(left, right)[0, 1])


def _smooth_gains(gains: np.ndarray, window: int = 3) -> np.ndarray:
    if window <= 1 or gains.size < 2:
        return gains
    kernel = np.ones(window, dtype=np.float64) / window
    return np.convolve(gains, kernel, mode="same")


def preserve_stereo_width(
    original: np.ndarray,
    mastered: np.ndarray,
    sample_rate: int,
    *,
    n_bands: int = _N_BANDS,
    gain_clamp_db: float = _GAIN_CLAMP_DB,
    mono_threshold_db: float = _MONO_SIDE_MID_THRESHOLD_DB,
) -> np.ndarray:
    """Restore per-band side/mid energy ratios from the original onto the mastered signal."""
    original = np.asarray(original, dtype=np.float64)
    mastered = np.asarray(mastered, dtype=np.float64)
    if original.ndim != 2 or mastered.ndim != 2 or original.shape[1] != 2:
        return mastered
    if mastered.shape[1] != 2:
        return mastered

    n = min(len(original), len(mastered))
    if n < 256:
        return mastered[:n]
    original = original[:n]
    mastered = mastered[:n]

    if stereo_side_mid_ratio_db(original) < mono_threshold_db:
        return mastered

    orig_mid, orig_side = lr_to_ms(original)
    out_mid, out_side = lr_to_ms(mastered)

    nperseg = min(4096, max(512, (n // 8) | 1))
    noverlap = nperseg // 2

    def _stft(ch: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        freqs, times, z = signal.stft(
            ch,
            fs=sample_rate,
            nperseg=nperseg,
            noverlap=noverlap,
            boundary="zeros",
            padded=True,
        )
        return freqs, times, z

    f, _t, z_om = _stft(orig_mid)
    _f, _t, z_os = _stft(orig_side)
    _f, _t, z_pm = _stft(out_mid)
    _f, _t, z_ps = _stft(out_side)

    nyquist = sample_rate / 2.0
    edges = np.geomspace(30.0, max(nyquist - 1.0, 60.0), n_bands + 1)
    band_gain_linear = np.ones(len(f), dtype=np.float64)
    orig_sm_ratio = np.ones(len(f), dtype=np.float64)

    for i in range(n_bands):
        lo, hi = edges[i], edges[i + 1]
        mask = (f >= lo) & (f < hi)
        if not np.any(mask):
            continue
        om = float(np.mean(np.abs(z_om[mask]) ** 2))
        os_ = float(np.mean(np.abs(z_os[mask]) ** 2))
        pm = float(np.mean(np.abs(z_pm[mask]) ** 2))
        ps = float(np.mean(np.abs(z_ps[mask]) ** 2))
        ratio_orig = np.sqrt(os_ / max(om, 1e-20))
        ratio_out = np.sqrt(ps / max(pm, 1e-20))
        gain = ratio_orig / max(ratio_out, 1e-20)
        gain_db = float(np.clip(20.0 * np.log10(max(gain, 1e-20)), -gain_clamp_db, gain_clamp_db))
        band_gain_linear[mask] = 10.0 ** (gain_db / 20.0)
        orig_sm_ratio[mask] = ratio_orig

    band_gain_linear = _smooth_gains(band_gain_linear, window=3)
    orig_sm_ratio = _smooth_gains(orig_sm_ratio, window=3)

    phase = np.angle(z_ps)
    phase = np.where(np.abs(z_ps) > 1e-9, phase, np.angle(z_os))
    mag_out = np.abs(z_ps)
    mag_target = np.abs(z_pm) * orig_sm_ratio[:, np.newaxis]
    # Blend: scale existing side where present, otherwise synthesize from mid × orig S/M ratio.
    use_synth = mag_out < (mag_target * 0.05)
    mag_final = np.where(use_synth, mag_target, mag_out * band_gain_linear[:, np.newaxis])
    z_ps_scaled = mag_final * np.exp(1j * phase)

    _, side_corr = signal.istft(
        z_ps_scaled,
        fs=sample_rate,
        nperseg=nperseg,
        noverlap=noverlap,
        boundary="zeros",
    )
    side_corr = side_corr[:n]
    out_mid = out_mid[:n]
    if len(side_corr) < n:
        side_corr = np.pad(side_corr, (0, n - len(side_corr)))
    elif len(side_corr) > n:
        side_corr = side_corr[:n]
    return ms_to_lr(out_mid, side_corr)


def stereo_width_label(mode: str | None) -> str:
    if mode == "preserve":
        return "Keep original width"
    return "Follow reference (default)"
