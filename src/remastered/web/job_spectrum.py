"""Long-term spectrum comparison (source, reference, mastered) for completed jobs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import soundfile as sf

from remastered.treble_cutoff import welch_magnitude_db
from remastered.web.integrated_loudness import measure_integrated_lufs
from remastered.web.media import media_exists
from remastered.web.reference_resolve import reference_cache_wav
from remastered.web.reference_youtube import is_youtube_reference_job
from remastered.web.settings import WebSettings

if TYPE_CHECKING:
    from remastered.web.db import JobStore

logger = logging.getLogger(__name__)

SPECTRUM_JSON_VERSION = 2
LOUDNESS_TARGET_LUFS = -23.0
FREQ_MIN_HZ = 20.0
FREQ_MAX_HZ = 20_000.0
NUM_POINTS = 100
MEDIAN_BAND_HZ = (500.0, 2_000.0)
_OCT_FRAC = 1.0 / 3.0


def _to_mono_mid(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        return audio
    if audio.shape[1] == 1:
        return audio[:, 0]
    return 0.5 * (audio[:, 0] + audio[:, 1])


def _path_looks_like_wav(path: Path) -> bool:
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < 44:
        return False
    try:
        with path.open("rb") as handle:
            head = handle.read(12)
    except OSError:
        return False
    return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"


def _cache_is_fresh(path: Path, retention_days: int) -> bool:
    if not path.is_file():
        return False
    try:
        import time

        age_seconds = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age_seconds <= retention_days * 86400


def reference_wav_if_available(job: dict, work_dir: Path, *, retention_days: int) -> Path | None:
    """Resolve reference WAV without triggering downloads (lazy backfill safe)."""
    if is_youtube_reference_job(job):
        video_id = (job.get("reference_video_id") or "").strip()
        if not video_id:
            return None
        cache_path = reference_cache_wav(work_dir, video_id)
        return cache_path if _cache_is_fresh(cache_path, retention_days) else None
    ref = Path(str(job.get("reference_path") or ""))
    return ref if ref.is_file() else None


def _log_freq_grid() -> np.ndarray:
    return np.geomspace(FREQ_MIN_HZ, FREQ_MAX_HZ, NUM_POINTS)


def loudness_offset_db(integrated_lufs: float) -> float:
    """dB to add to a tone-matched curve for actual integrated level (LUFS vs -23)."""
    return float(integrated_lufs) - LOUDNESS_TARGET_LUFS


def spectrum_has_level_data(payload: dict[str, Any]) -> bool:
    """True when stored JSON includes per-curve integrated LUFS (v2+)."""
    if int(payload.get("v", 0)) < 2:
        return False
    lu = payload.get("lu")
    if not isinstance(lu, list) or len(lu) != 3:
        return False
    return all(isinstance(x, (int, float)) and np.isfinite(float(x)) for x in lu)


def _lufs_gain(linear_audio: np.ndarray, measured_lufs: float | None) -> np.ndarray:
    if measured_lufs is None or not np.isfinite(measured_lufs):
        return linear_audio
    delta_db = LOUDNESS_TARGET_LUFS - measured_lufs
    return linear_audio * (10.0 ** (delta_db / 20.0))


def _load_mono_mid(path: Path) -> tuple[np.ndarray, int] | None:
    if not path.is_file() or not _path_looks_like_wav(path):
        return None
    try:
        audio, sr = sf.read(str(path), always_2d=True, dtype="float64")
    except Exception:  # noqa: BLE001
        logger.debug("Skip spectrum WAV read for %s", path)
        return None
    if audio.size == 0:
        return None
    mono = _to_mono_mid(audio)
    return mono.astype(np.float64, copy=False), int(sr)


def _smooth_third_octave(
    freqs_hz: np.ndarray,
    mag_db: np.ndarray,
    targets_hz: np.ndarray,
) -> np.ndarray:
    """Average Welch bins in ~1/3-octave bands around each target (power domain)."""
    out = np.full(targets_hz.shape, np.nan, dtype=np.float64)
    for i, f0 in enumerate(targets_hz):
        lo = f0 / (2.0 ** (_OCT_FRAC / 2.0))
        hi = f0 * (2.0 ** (_OCT_FRAC / 2.0))
        mask = (freqs_hz >= lo) & (freqs_hz <= hi)
        if not np.any(mask):
            continue
        segment = mag_db[mask]
        if np.all(np.isnan(segment)):
            continue
        lin = 10.0 ** (np.nanmean(segment) / 10.0)
        out[i] = 10.0 * np.log10(max(lin, 1e-30))
    return out


def _spectrum_db_relative(mag_db: np.ndarray, centers_hz: np.ndarray) -> np.ndarray:
    lo, hi = MEDIAN_BAND_HZ
    band = (centers_hz >= lo) & (centers_hz <= hi)
    if not np.any(band):
        return mag_db - np.nanmedian(mag_db)
    ref = float(np.nanmedian(mag_db[band]))
    return mag_db - ref


def compute_spectrum_comparison(
    *,
    original_wav: Path,
    reference_wav: Path,
    mastered_wav: Path,
    ffmpeg_path: str = "ffmpeg",
) -> dict[str, Any] | None:
    """Return compact comparison payload or None on failure."""
    paths = (original_wav, reference_wav, mastered_wav)
    curves_db: list[np.ndarray] = []
    integrated_lufs: list[float] = []
    grid = _log_freq_grid()

    for wav in paths:
        loaded = _load_mono_mid(wav)
        if loaded is None:
            return None
        mono, sr = loaded
        lufs = measure_integrated_lufs(wav, ffmpeg_path=ffmpeg_path)
        if lufs is None or not np.isfinite(lufs):
            return None
        integrated_lufs.append(round(float(lufs), 1))
        mono = _lufs_gain(mono, lufs)
        freqs, db = welch_magnitude_db(mono, sr)
        binned = _smooth_third_octave(freqs, db, grid)
        if np.any(np.isnan(binned)):
            valid = ~np.isnan(binned)
            if np.count_nonzero(valid) < NUM_POINTS // 2:
                return None
            binned = np.interp(
                grid,
                grid[valid],
                binned[valid],
                left=np.nan,
                right=np.nan,
            )
        rel = _spectrum_db_relative(binned, grid)
        curves_db.append(rel)

    def _round_list(arr: np.ndarray) -> list[float]:
        cleaned = np.where(np.isnan(arr), 0.0, arr)
        return [round(float(x), 1) for x in cleaned]

    freqs_out = [round(float(f), 1) if f < 1000 else round(float(f), 0) for f in grid]
    return {
        "v": SPECTRUM_JSON_VERSION,
        "f": freqs_out,
        "o": _round_list(curves_db[0]),
        "r": _round_list(curves_db[1]),
        "m": _round_list(curves_db[2]),
        "lu": integrated_lufs,
    }


def spectrum_json_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def parse_stored_spectrum(raw: object) -> dict[str, Any] | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    for key in ("f", "o", "r", "m"):
        if key not in data or not isinstance(data[key], list):
            return None
    if len(data["f"]) != len(data["o"]) == len(data["r"]) == len(data["m"]):
        return None
    return data


def measure_and_store_job_spectrum(
    job_id: str,
    store: JobStore,
    *,
    original_wav: Path | None,
    reference_wav: Path | None,
    mastered_wav: Path | None,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    if not (original_wav and reference_wav and mastered_wav):
        return
    try:
        payload = compute_spectrum_comparison(
            original_wav=original_wav,
            reference_wav=reference_wav,
            mastered_wav=mastered_wav,
            ffmpeg_path=ffmpeg_path,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Spectrum computation failed for job %s", job_id, exc_info=True)
        return
    if payload is None:
        return
    try:
        store.update_job(
            job_id,
            spectrum_comparison_json=json.dumps(payload, separators=(",", ":")),
        )
    except Exception:  # noqa: BLE001
        logger.warning("Could not store spectrum for job %s", job_id, exc_info=True)


def resolve_spectrum_for_job(
    job: dict,
    *,
    store: JobStore | None,
    work_dir: Path,
    ffmpeg_path: str = "ffmpeg",
) -> dict[str, Any] | None:
    """Return comparison JSON for UI/API; lazy-compute when media exists."""
    if job.get("status") != "completed":
        return None

    cached = parse_stored_spectrum(job.get("spectrum_comparison_json"))
    if cached is not None and spectrum_has_level_data(cached):
        return cached

    audio_key = job.get("audio_path")
    mastered_key = job.get("mastered_path")
    if not (
        media_exists(audio_key, job, work_dir)
        and media_exists(mastered_key, job, work_dir)
    ):
        return None

    ws = WebSettings()
    ref_path = reference_wav_if_available(job, work_dir, retention_days=ws.job_retention_days)
    if ref_path is None:
        return None

    original_path = Path(str(audio_key))
    mastered_path = Path(str(mastered_key))

    try:
        payload = compute_spectrum_comparison(
            original_wav=original_path,
            reference_wav=ref_path,
            mastered_wav=mastered_path,
            ffmpeg_path=ffmpeg_path,
        )
    except Exception:  # noqa: BLE001
        logger.debug("Lazy spectrum failed for job %s", job.get("id"))
        return None

    if payload is None:
        return None

    if store is not None and job.get("id"):
        try:
            blob = json.dumps(payload, separators=(",", ":"))
            store.update_job(str(job["id"]), spectrum_comparison_json=blob)
            job["spectrum_comparison_json"] = blob
        except Exception:  # noqa: BLE001
            logger.warning("Could not cache spectrum for job %s", job.get("id"), exc_info=True)

    return payload


def curve_from_mono(mono: np.ndarray, sample_rate: int) -> np.ndarray:
    """Welch → 1/3-octave log grid → dB relative to 500–2 kHz (tests)."""
    freqs, db = welch_magnitude_db(mono, sample_rate)
    grid = _log_freq_grid()
    binned = _smooth_third_octave(freqs, db, grid)
    return _spectrum_db_relative(binned, grid)