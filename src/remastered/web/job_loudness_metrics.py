"""Persist and resolve integrated loudness for download-volume UI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from remastered.web.ab_loudness_match import (
    format_remaster_volume_of_original_line,
    remaster_volume_percent_of_original,
)
from remastered.web.integrated_loudness import measure_integrated_lufs
from remastered.web.media import media_exists

if TYPE_CHECKING:
    from remastered.web.db import JobStore

logger = logging.getLogger(__name__)


def _finite_lufs(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not (out == out):  # NaN
        return None
    return out


def measure_and_store_job_lufs(
    job_id: str,
    store: JobStore,
    *,
    audio_wav: Path | None,
    mastered_wav: Path | None,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    """Measure original and mastered WAV LUFS once; ignore failures."""
    l_orig = measure_integrated_lufs(audio_wav, ffmpeg_path=ffmpeg_path) if audio_wav else None
    l_mast = measure_integrated_lufs(mastered_wav, ffmpeg_path=ffmpeg_path) if mastered_wav else None
    if l_orig is None and l_mast is None:
        return
    fields: dict[str, float] = {}
    if l_orig is not None:
        fields["lufs_original"] = l_orig
    if l_mast is not None:
        fields["lufs_mastered_wav"] = l_mast
    try:
        store.update_job(job_id, **fields)
    except Exception:  # noqa: BLE001
        logger.warning("Could not store LUFS metrics for job %s", job_id, exc_info=True)


def resolve_remaster_volume_of_original_line(
    job: dict,
    *,
    store: JobStore | None,
    work_dir: Path,
    ffmpeg_path: str = "ffmpeg",
) -> str | None:
    """Return user-facing download volume line, measuring lazily for older jobs when safe."""
    l_orig = _finite_lufs(job.get("lufs_original"))
    l_mast = _finite_lufs(job.get("lufs_mastered_wav"))

    if l_orig is None or l_mast is None:
        audio_key = job.get("audio_path")
        mastered_key = job.get("mastered_path")
        if not (
            media_exists(audio_key, job, work_dir)
            and media_exists(mastered_key, job, work_dir)
        ):
            return None
        audio_path = Path(str(audio_key))
        mastered_path = Path(str(mastered_key))
        try:
            if l_orig is None:
                l_orig = measure_integrated_lufs(audio_path, ffmpeg_path=ffmpeg_path)
            if l_mast is None:
                l_mast = measure_integrated_lufs(mastered_path, ffmpeg_path=ffmpeg_path)
        except Exception:  # noqa: BLE001
            logger.warning("Lazy LUFS measure failed for job %s", job.get("id"), exc_info=True)
            return None
        if store is not None and job.get("id"):
            fields: dict[str, float] = {}
            if l_orig is not None and job.get("lufs_original") is None:
                fields["lufs_original"] = l_orig
            if l_mast is not None and job.get("lufs_mastered_wav") is None:
                fields["lufs_mastered_wav"] = l_mast
            if fields:
                try:
                    store.update_job(str(job["id"]), **fields)
                    job.update(fields)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Could not cache LUFS for job %s", job.get("id"), exc_info=True
                    )

    percent = remaster_volume_percent_of_original(
        l_mast if l_mast is not None else float("nan"),
        l_orig if l_orig is not None else float("nan"),
    )
    return format_remaster_volume_of_original_line(percent)
