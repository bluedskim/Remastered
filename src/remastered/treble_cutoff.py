"""Detect reference encoder lowpass and hold matching EQ gain above the cutoff."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

MEDIAN_BAND_HZ = (2_000.0, 8_000.0)
WELCH_NPERSEG = 8192

# Rule 1 — first −20 dB crossing (relative to 2–8 kHz median).
FC_SEARCH_MIN_HZ = 8_000.0
FC_SEARCH_MAX_HZ = 16_500.0
FC_CROSSING_DB = -20.0

# Rule 2 — cut depth immediately above fc.
DEPTH_BAND_START_ABOVE_FC_HZ = 1_000.0
DEPTH_BAND_END_ABOVE_FC_HZ = 5_000.0
DEPTH_BAND_CAP_HZ = 19_500.0
DEPTH_MIN_WIDTH_HZ = 2_000.0
DEPTH_POWER_MEAN_MAX_DB = -28.0

# Rule 3 — transition width between −10 dB and −30 dB crossings.
TRANSITION_CROSSING_HIGH_DB = -10.0
TRANSITION_CROSSING_LOW_DB = -30.0
TRANSITION_MAX_WIDTH_HZ = 4_500.0
TRANSITION_LOW_CAP_HZ = 19_500.0

# Rule 4 — no mid-treble recovery before fc.
RECOVERY_BAND_MIN_HZ = 10_000.0
RECOVERY_MAX_DB = -3.0

# Rule 5 — source has more air than reference above fc.
DIVERGENCE_MIN_DB = 10.0
DIVERGENCE_BAND_ABOVE_FC_HZ = 4_000.0
DIVERGENCE_BAND_CAP_HZ = 20_000.0

# Binned PSD (matches production verification curves).
PSD_BIN_HZ = 50.0
PSD_BIN_START_HZ = 5_000.0
PSD_BIN_END_HZ = 22_000.0

DEFAULT_TRANSITION_HZ = 750.0


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


def psd_bin_centers_hz() -> np.ndarray:
    half = PSD_BIN_HZ / 2.0
    return np.arange(PSD_BIN_START_HZ + half, PSD_BIN_END_HZ + half, PSD_BIN_HZ)


def binned_relative_psd_from_stereo(
    audio: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Welch spectrum, dB relative to 2–8 kHz median, 50 Hz bins (5–22 kHz)."""
    mono = _rms_normalize(_stereo_to_mid(audio))
    freqs, db = welch_magnitude_db(mono, sample_rate)
    lo, hi = MEDIAN_BAND_HZ
    band = (freqs >= lo) & (freqs <= hi)
    if not np.any(band):
        centers = psd_bin_centers_hz()
        return centers, np.full_like(centers, np.nan, dtype=np.float64)
    median_db = float(np.median(db[band]))
    db_rel = db - median_db
    centers = psd_bin_centers_hz()
    binned = np.interp(centers, freqs, db_rel, left=np.nan, right=np.nan)
    return centers, binned


def _first_downward_crossing(
    freqs: np.ndarray,
    levels_db: np.ndarray,
    threshold_db: float,
    *,
    f_min: float,
    f_max: float,
) -> float | None:
    for i in range(1, len(freqs)):
        f = float(freqs[i])
        if f < f_min:
            continue
        if f > f_max:
            break
        if levels_db[i - 1] > threshold_db and levels_db[i] <= threshold_db:
            return f
    return None


def _power_mean_db(levels_db: np.ndarray) -> float:
    lin = 10.0 ** (levels_db / 10.0)
    return float(10.0 * np.log10(max(float(np.mean(lin)), 1e-30)))


def detect_hard_reference_cutoff_from_relative_psd(
    freqs_hz: np.ndarray,
    level_db_rel: np.ndarray,
) -> float | None:
    """Return fc (Hz) when all reference cut rules pass, else None."""
    if np.any(np.isnan(level_db_rel)):
        return None

    fc_hz = _first_downward_crossing(
        freqs_hz,
        level_db_rel,
        FC_CROSSING_DB,
        f_min=FC_SEARCH_MIN_HZ,
        f_max=FC_SEARCH_MAX_HZ,
    )
    if fc_hz is None:
        return None

    # Rule 2 — depth
    depth_lo = fc_hz + DEPTH_BAND_START_ABOVE_FC_HZ
    depth_hi = min(fc_hz + DEPTH_BAND_END_ABOVE_FC_HZ, DEPTH_BAND_CAP_HZ)
    if depth_hi - depth_lo < DEPTH_MIN_WIDTH_HZ:
        return None
    depth_mask = (freqs_hz >= depth_lo) & (freqs_hz <= depth_hi)
    if not np.any(depth_mask):
        return None
    if _power_mean_db(level_db_rel[depth_mask]) > DEPTH_POWER_MEAN_MAX_DB:
        return None

    # Rule 3 — transition width
    f10 = _first_downward_crossing(
        freqs_hz,
        level_db_rel,
        TRANSITION_CROSSING_HIGH_DB,
        f_min=PSD_BIN_START_HZ,
        f_max=float(freqs_hz[-1]),
    )
    if f10 is None:
        return None
    f30 = _first_downward_crossing(
        freqs_hz,
        level_db_rel,
        TRANSITION_CROSSING_LOW_DB,
        f_min=f10,
        f_max=TRANSITION_LOW_CAP_HZ,
    )
    if f30 is None or f30 >= TRANSITION_LOW_CAP_HZ:
        return None
    if f30 - f10 > TRANSITION_MAX_WIDTH_HZ:
        return None

    # Rule 4 — no mid-treble recovery
    pre_mask = (freqs_hz >= RECOVERY_BAND_MIN_HZ) & (freqs_hz <= fc_hz)
    if not np.any(pre_mask):
        return None
    if float(np.max(level_db_rel[pre_mask])) > RECOVERY_MAX_DB:
        return None

    return fc_hz


def detect_hard_reference_cutoff(
    reference: np.ndarray,
    sample_rate: int,
) -> float | None:
    """Return cutoff frequency in Hz, or None if no lossy encoder lowpass is detected."""
    freqs, rel = binned_relative_psd_from_stereo(reference, sample_rate)
    return detect_hard_reference_cutoff_from_relative_psd(freqs, rel)


def source_ref_treble_divergence_from_relative_psd(
    freqs_hz: np.ndarray,
    rel_src: np.ndarray,
    rel_ref: np.ndarray,
    fc_hz: float,
) -> float:
    hi = min(fc_hz + DIVERGENCE_BAND_ABOVE_FC_HZ, DIVERGENCE_BAND_CAP_HZ)
    mask = (freqs_hz >= fc_hz) & (freqs_hz <= hi)
    if not np.any(mask):
        return -999.0
    return float(np.mean(rel_src[mask]) - np.mean(rel_ref[mask]))


def source_ref_treble_divergence_db(
    target: np.ndarray,
    reference: np.ndarray,
    sample_rate: int,
    fc_hz: float,
) -> float:
    """Mean relative level (source − ref) over [fc, min(fc+4 kHz, 20 kHz)]."""
    freq_t, rel_t = binned_relative_psd_from_stereo(target, sample_rate)
    freq_r, rel_r = binned_relative_psd_from_stereo(reference, sample_rate)
    if not np.allclose(freq_t, freq_r):
        raise ValueError("mismatched PSD bin grids")
    return source_ref_treble_divergence_from_relative_psd(freq_t, rel_t, rel_r, fc_hz)


def source_has_treble_above(
    target: np.ndarray,
    reference: np.ndarray,
    sample_rate: int,
    fc_hz: float,
) -> bool:
    return (
        source_ref_treble_divergence_db(target, reference, sample_rate, fc_hz)
        >= DIVERGENCE_MIN_DB
    )


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
        if not source_has_treble_above(target, reference, sample_rate, fc):
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