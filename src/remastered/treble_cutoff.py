"""Detect reference encoder lowpass and hold matching EQ gain above the cutoff."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

# Detection thresholds (see module doc / PR description).
MEDIAN_BAND_HZ = (2_000.0, 8_000.0)
SEARCH_MIN_HZ = 8_000.0
MAX_CUTOFF_HZ = 19_500.0
BELOW_MEDIAN_DB = 20.0
STEEP_DROP_DB = 15.0
STEEP_WINDOW_HZ = 1_000.0
DEFAULT_TRANSITION_HZ = 750.0
SOURCE_TREBLE_MARGIN_DB = 20.0

WELCH_NPERSEG = 8192


def treble_hold_label(enabled: bool | None) -> str:
    if enabled is False:
        return "Off (match reference treble fully)"
    return "On (keep source air above reference cutoff, default)"


def _stereo_to_mid(audio: np.ndarray) -> np.ndarray:
    if audio.ndim != 2 or audio.shape[1] != 2:
        raise ValueError("expected stereo audio shaped (n_samples, 2)")
    return 0.5 * (audio[:, 0] + audio[:, 1])


def _rms_normalize(mono: np.ndarray, target_rms: float = 0.1) -> np.ndarray:
    rms = float(np.sqrt(np.mean(mono * mono)))
    if rms < 1e-12:
        return mono
    return mono * (target_rms / rms)


def welch_magnitude_db(mono: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    nperseg = min(WELCH_NPERSEG, max(256, len(mono) // 4))
    freqs, psd = signal.welch(
        mono,
        fs=sample_rate,
        nperseg=nperseg,
        noverlap=nperseg // 2,
        window="hann",
        scaling="density",
    )
    mag = np.sqrt(np.maximum(psd, 0.0))
    mag_db = 20.0 * np.log10(np.maximum(mag, 1e-20))
    return freqs, mag_db


def _interp_db(freqs: np.ndarray, db: np.ndarray, hz: float) -> float:
    if hz <= freqs[0]:
        return float(db[0])
    if hz >= freqs[-1]:
        return float(db[-1])
    return float(np.interp(hz, freqs, db))


def _is_steep_cutoff(freqs: np.ndarray, db: np.ndarray, fc_hz: float) -> bool:
    """True when level falls >= STEEP_DROP_DB across STEEP_WINDOW_HZ ending at the cutoff."""
    below = _interp_db(freqs, db, fc_hz - STEEP_WINDOW_HZ)
    at_or_above = _interp_db(freqs, db, min(fc_hz + 200.0, freqs[-1]))
    return (below - at_or_above) >= STEEP_DROP_DB


def detect_hard_reference_cutoff(
    reference: np.ndarray,
    sample_rate: int,
) -> float | None:
    """Return cutoff frequency in Hz, or None if no hard encoder lowpass is detected."""
    mono = _rms_normalize(_stereo_to_mid(reference))
    freqs, db = welch_magnitude_db(mono, sample_rate)
    lo, hi = MEDIAN_BAND_HZ
    band = (freqs >= lo) & (freqs <= hi)
    if not np.any(band):
        return None
    median_db = float(np.median(db[band]))
    threshold = median_db - BELOW_MEDIAN_DB

    above = freqs >= SEARCH_MIN_HZ
    idx_candidates = np.where(above & (db <= threshold))[0]
    if idx_candidates.size == 0:
        return None

    fc_hz: float | None = None
    for idx in idx_candidates:
        f = float(freqs[idx])
        if f >= MAX_CUTOFF_HZ:
            continue
        tail = (freqs >= f) & (freqs <= MAX_CUTOFF_HZ)
        if not np.all(db[tail] <= threshold + 1.0):
            continue
        if _is_steep_cutoff(freqs, db, f):
            fc_hz = f
            break

    return fc_hz


def source_has_treble_above(
    target: np.ndarray,
    sample_rate: int,
    fc_hz: float,
) -> bool:
    """True when the source still has meaningful energy above fc_hz."""
    mono = _rms_normalize(_stereo_to_mid(target))
    freqs, db = welch_magnitude_db(mono, sample_rate)
    lo, hi = MEDIAN_BAND_HZ
    band = (freqs >= lo) & (freqs <= hi)
    if not np.any(band):
        return False
    median_db = float(np.median(db[band]))
    threshold = median_db - SOURCE_TREBLE_MARGIN_DB
    check = (freqs >= fc_hz) & (freqs <= min(fc_hz + 4_000.0, freqs[-1]))
    if not np.any(check):
        return False
    return bool(np.max(db[check]) > threshold)


@dataclass(frozen=True)
class TrebleHoldPlan:
    fc_hz: float
    transition_hz: float = DEFAULT_TRANSITION_HZ

    @staticmethod
    def resolve(
        reference: np.ndarray,
        target: np.ndarray,
        sample_rate: int,
    ) -> TrebleHoldPlan | None:
        fc = detect_hard_reference_cutoff(reference, sample_rate)
        if fc is None:
            return None
        if not source_has_treble_above(target, sample_rate, fc):
            return None
        return TrebleHoldPlan(fc_hz=fc)


def apply_gain_hold_above_cutoff(
    freqs_hz: np.ndarray,
    matching_gain: np.ndarray,
    plan: TrebleHoldPlan,
) -> np.ndarray:
    """Flatten matching EQ gain above cutoff with a cosine transition band."""
    out = np.array(matching_gain, copy=True, dtype=np.float64)
    fc = plan.fc_hz
    half = plan.transition_hz * 0.5
    f_low = fc - half
    f_high = fc + half

    hold_idx = np.searchsorted(freqs_hz, f_low, side="left") - 1
    hold_idx = max(1, min(hold_idx, len(out) - 1))
    hold_gain = float(out[hold_idx])

    for i, f in enumerate(freqs_hz):
        if f <= f_low:
            continue
        if f >= f_high:
            out[i] = hold_gain
            continue
        t = (f - f_low) / max(plan.transition_hz, 1e-6)
        blend = 0.5 - 0.5 * np.cos(np.pi * t)
        out[i] = (1.0 - blend) * out[i] + blend * hold_gain
    return out


def get_fir_with_optional_treble_hold(
    target_loudest_pieces: np.ndarray,
    reference_loudest_pieces: np.ndarray,
    name: str,
    config,
    plan: TrebleHoldPlan | None,
) -> np.ndarray:
    """Matchering-compatible FIR, optionally flattening EQ gain above a reference cutoff."""
    from matchering.stage_helpers.match_frequencies import (  # noqa: PLC0415
        __smooth_exponentially,
    )
    from matchering.stage_helpers.match_frequencies import __average_fft as _avg_fft

    fft_size = config.fft_size
    sr = config.internal_sample_rate

    target_average_fft = _avg_fft(target_loudest_pieces, sr, fft_size)
    reference_average_fft = _avg_fft(reference_loudest_pieces, sr, fft_size)
    np.maximum(config.min_value, target_average_fft, out=target_average_fft)
    matching_fft = reference_average_fft / target_average_fft
    matching_fft_filtered = __smooth_exponentially(matching_fft, config)

    if plan is not None:
        grid_linear = sr * 0.5 * np.linspace(0, 1, fft_size // 2 + 1)
        matching_fft_filtered = apply_gain_hold_above_cutoff(
            grid_linear, matching_fft_filtered, plan
        )

    fir = np.fft.irfft(matching_fft_filtered)
    fir = np.fft.ifftshift(fir) * signal.windows.hann(len(fir))
    return fir


def match_frequencies_with_treble_hold(
    target_mid: np.ndarray,
    target_side: np.ndarray,
    target_mid_loudest_pieces: np.ndarray,
    reference_mid_loudest_pieces: np.ndarray,
    target_side_loudest_pieces: np.ndarray,
    reference_side_loudest_pieces: np.ndarray,
    config,
    plan: TrebleHoldPlan | None,
) -> tuple[np.ndarray, np.ndarray]:
    from matchering.stage_helpers.match_frequencies import convolve

    mid_fir = get_fir_with_optional_treble_hold(
        target_mid_loudest_pieces,
        reference_mid_loudest_pieces,
        "mid",
        config,
        plan,
    )
    side_fir = get_fir_with_optional_treble_hold(
        target_side_loudest_pieces,
        reference_side_loudest_pieces,
        "side",
        config,
        plan,
    )
    return convolve(target_mid, mid_fir, target_side, side_fir)


def format_cutoff_hz(fc_hz: float) -> str:
    if fc_hz >= 10_000:
        return f"{fc_hz / 1000:.1f} kHz"
    return f"{fc_hz:.0f} Hz" if fc_hz >= 1000 else f"{fc_hz:.0f} Hz"


def treble_hold_log_message(plan: TrebleHoldPlan | None, detected_fc: float | None) -> str:
    if plan is not None:
        return (
            f"Reference treble is cut off above {format_cutoff_hz(plan.fc_hz)}; "
            "kept the source's treble above that point."
        )
    if detected_fc is not None:
        return (
            f"Reference treble is cut off above {format_cutoff_hz(detected_fc)}, "
            "but the source has no extra air there; treble matched normally."
        )
    return "No reference cutoff detected; treble matched normally."