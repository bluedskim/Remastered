"""Detect reference encoder lowpass and hold matching EQ gain above the cutoff."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

# Detection thresholds — tuned for lossy codec lowpass (e.g. YouTube), not gentle masters.
MEDIAN_BAND_HZ = (2_000.0, 8_000.0)
SEARCH_MIN_HZ = 8_000.0
MAX_CUTOFF_HZ = 19_500.0
BELOW_MEDIAN_DB = 20.0
# Transition: ≥15 dB fall within ~2.5 kHz above the −20 dB crossing (~6 dB/kHz codec knee).
TRANSITION_WINDOW_HZ = 2_500.0
TRANSITION_LOOKBACK_HZ = 500.0
MIN_TRANSITION_DROP_DB = 15.0
MIN_AVG_SLOPE_DB_PER_KHZ = 5.0
# Plateau: from fc+2.5 kHz to 19.5 kHz, flat codec floor (≈35–40 dB below body).
PLATEAU_START_ABOVE_FC_HZ = 2_500.0
FLOOR_MARGIN_DB = 34.0
PLATEAU_RECOVERY_DB = 4.0
PLATEAU_MAX_SPAN_DB = 7.0
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


def _has_codec_plateau(
    freqs: np.ndarray,
    db: np.ndarray,
    median_db: float,
    fc_hz: float,
) -> bool:
    """High band sits on a floor ≥FLOOR_MARGIN_DB below the body, without recovering."""
    plateau_lo = fc_hz + PLATEAU_START_ABOVE_FC_HZ
    if plateau_lo >= MAX_CUTOFF_HZ:
        return False
    plateau = (freqs >= plateau_lo) & (freqs <= MAX_CUTOFF_HZ)
    if not np.any(plateau):
        return False
    tail = db[plateau]
    floor_db = median_db - FLOOR_MARGIN_DB
    tail_max = float(np.max(tail))
    tail_span = float(np.max(tail) - np.min(tail))
    if tail_max > floor_db + PLATEAU_RECOVERY_DB:
        return False
    # Natural masters keep measurable HF energy with a wide sloping span above ~median−28 dB.
    if tail_max > median_db - 28.0 and tail_span > PLATEAU_MAX_SPAN_DB:
        return False
    return float(np.median(tail)) <= floor_db + 1.5


def _has_codec_transition(freqs: np.ndarray, db: np.ndarray, fc_hz: float) -> bool:
    """Enough slope into the cut for a codec knee (not a slow master tilt)."""
    f_before = max(SEARCH_MIN_HZ, fc_hz - TRANSITION_LOOKBACK_HZ)
    f_after = min(fc_hz + TRANSITION_WINDOW_HZ, float(freqs[-1]))
    if f_after <= f_before + 500.0:
        return False
    drop_db = _interp_db(freqs, db, f_before) - _interp_db(freqs, db, f_after)
    span_khz = (f_after - f_before) / 1000.0
    avg_slope = drop_db / span_khz
    return drop_db >= MIN_TRANSITION_DROP_DB or avg_slope >= MIN_AVG_SLOPE_DB_PER_KHZ


def detect_hard_reference_cutoff(
    reference: np.ndarray,
    sample_rate: int,
) -> float | None:
    """Return cutoff frequency in Hz, or None if no lossy encoder lowpass is detected."""
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
            break
        if idx > 0 and db[idx - 1] > threshold:
            fc_hz = f
            break
    if fc_hz is None:
        fc_hz = float(freqs[int(idx_candidates[0])])
        if fc_hz >= MAX_CUTOFF_HZ:
            return None

    tail = (freqs >= fc_hz) & (freqs <= MAX_CUTOFF_HZ)
    if not np.all(db[tail] <= threshold + 2.5):
        return None
    if not _has_codec_transition(freqs, db, fc_hz):
        return None
    if not _has_codec_plateau(freqs, db, median_db, fc_hz):
        return None
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