"""Objective metrics for limiter comparison (not audibility claims)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from remastered.web.true_peak_protection import (
    _encode_playback_aac,
    _measure_true_peak_dbtp,
    apply_final_true_peak_protection,
)


def _lufs_integrated(path: Path, *, ffmpeg: str = "ffmpeg") -> float | None:
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "ebur128",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    for line in (proc.stderr or "").splitlines():
        if "I:" in line and "LUFS" in line:
            parts = line.split("I:")
            if len(parts) < 2:
                continue
            try:
                return float(parts[1].split("LUFS")[0].strip())
            except ValueError:
                continue
    return None


def array_stats(audio: np.ndarray) -> dict[str, Any]:
    flat = audio.astype(np.float64, copy=False).ravel()
    peak = float(np.max(np.abs(flat))) if flat.size else 0.0
    return {
        "samples": int(audio.shape[0]),
        "channels": int(audio.shape[1]) if audio.ndim == 2 else 1,
        "peak_linear": peak,
        "peak_dbfs": 20.0 * np.log10(peak) if peak > 0 else None,
        "count_ge_1p0": int(np.sum(np.abs(flat) >= 1.0 - 1e-12)),
        "nan": int(np.sum(np.isnan(flat))),
        "inf": int(np.sum(np.isinf(flat))),
    }


def measure_wav_file(path: Path, *, ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return {
        "path": str(path),
        "sample_rate": sr,
        "lufs_i": _lufs_integrated(path, ffmpeg=ffmpeg),
        "dbtp": _measure_true_peak_dbtp(path, ffmpeg_path=ffmpeg),
        **array_stats(audio),
    }


def measure_with_aac(wav: Path, *, ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    out = measure_wav_file(wav, ffmpeg=ffmpeg)
    with tempfile.TemporaryDirectory() as td:
        m4a = Path(td) / "check.m4a"
        _encode_playback_aac(wav, m4a, ffmpeg_path=ffmpeg)
        out["aac_dbtp"] = _measure_true_peak_dbtp(m4a, ffmpeg_path=ffmpeg)
        out["aac_lufs_i"] = _lufs_integrated(m4a, ffmpeg=ffmpeg)
    return out


def apply_delivery_tp_guard(
    raw_master: Path,
    out_master: Path,
    *,
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    import shutil

    shutil.copy2(raw_master, out_master)
    gain, peak_aac = apply_final_true_peak_protection(
        raw_master, out_master, ffmpeg_path=ffmpeg
    )
    return {"tp_gain_db": gain, "tp_aac_dbtp": peak_aac}
