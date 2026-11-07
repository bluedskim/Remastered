"""Objective metrics for limiter comparison (not audibility claims)."""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

import numpy as np
import soundfile as sf

from remastered.experiments.tp_aware_limiter.ffmpeg_ebur128 import measure_loudness
from remastered.web.settings import WebSettings
from remastered.web.true_peak_protection import (
    _encode_playback_aac,
    _measure_true_peak_dbtp,
    apply_final_true_peak_protection,
)


def _encode_aac_persistent(wav: Path, m4a: Path, *, ffmpeg: str = "ffmpeg") -> None:
    m4a.parent.mkdir(parents=True, exist_ok=True)
    _encode_playback_aac(wav, m4a, ffmpeg_path=ffmpeg)


def evaluate_tp_ceilings(
    wav_peak: float | None,
    aac_peak: float | None,
    *,
    strict_ceiling: float = -1.0,
    ws: WebSettings | None = None,
) -> dict[str, Any]:
    ws = ws or WebSettings()
    prod_wav_max = float(ws.true_peak_ceiling_dbtp)
    prod_aac_max = float(ws.true_peak_ceiling_dbtp + ws.true_peak_pass_headroom_db)
    return {
        "strict_ceiling_dbtp": strict_ceiling,
        "production_ceiling_wav_dbtp": prod_wav_max,
        "production_ceiling_aac_dbtp": prod_aac_max,
        "wav_peak_dbtp": wav_peak,
        "aac_peak_dbtp": aac_peak,
        "pass_strict_wav": wav_peak is not None and wav_peak <= strict_ceiling,
        "pass_strict_aac": aac_peak is not None and aac_peak <= strict_ceiling,
        "pass_production_rule": (
            wav_peak is not None
            and aac_peak is not None
            and wav_peak <= prod_wav_max
            and aac_peak <= prod_aac_max
        ),
    }


def array_stats(audio: np.ndarray) -> dict[str, Any]:
    flat = audio.astype(np.float64, copy=False).ravel()
    peak = float(np.max(np.abs(flat))) if flat.size else 0.0
    rms = float(np.sqrt(np.mean(flat**2))) if flat.size else 0.0
    crest = 20.0 * np.log10(peak / rms) if peak > 0 and rms > 0 else None
    return {
        "samples": int(audio.shape[0]),
        "channels": int(audio.shape[1]) if audio.ndim == 2 else 1,
        "peak_linear": peak,
        "peak_dbfs": 20.0 * np.log10(peak) if peak > 0 else None,
        "rms_dbfs": 20.0 * np.log10(rms) if rms > 0 else None,
        "crest_db": crest,
        "count_ge_1p0": int(np.sum(np.abs(flat) >= 1.0 - 1e-12)),
        "nan": int(np.sum(np.isnan(flat))),
        "inf": int(np.sum(np.isinf(flat))),
    }


def measure_wav_file(path: Path, *, ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    loud = measure_loudness(path, ffmpeg=ffmpeg)
    return {
        "path": str(path),
        "sample_rate": sr,
        **loud,
        **array_stats(audio),
    }


def measure_with_aac(
    wav: Path,
    *,
    ffmpeg: str = "ffmpeg",
    aac_path: Path | None = None,
) -> dict[str, Any]:
    out = measure_wav_file(wav, ffmpeg=ffmpeg)
    if aac_path is None:
        with tempfile.TemporaryDirectory() as td:
            m4a = Path(td) / "check.m4a"
            _encode_aac_persistent(wav, m4a, ffmpeg=ffmpeg)
            loud = measure_loudness(m4a, ffmpeg=ffmpeg)
    else:
        _encode_aac_persistent(wav, aac_path, ffmpeg=ffmpeg)
        loud = measure_loudness(aac_path, ffmpeg=ffmpeg)
    out["aac_path"] = str(aac_path) if aac_path else None
    out["aac_dbtp"] = loud.get("dbtp")
    out["aac_lufs_i"] = loud.get("lufs_i")
    out["aac_lra"] = loud.get("lra")
    out["aac_summary_present"] = loud.get("summary_present")
    if loud.get("parse_error"):
        out["aac_parse_error"] = loud["parse_error"]
    return out


def apply_delivery_tp_guard(
    raw_master: Path,
    out_master: Path,
    *,
    ffmpeg: str = "ffmpeg",
    ws: WebSettings | None = None,
) -> dict[str, Any]:
    """Run production ``apply_final_true_peak_protection``; post-verify; fail closed in metadata."""
    ws = ws or WebSettings()
    out_master.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    error: str | None = None
    gain = 0.0
    reported_aac: float | None = None
    try:
        gain, reported_aac = apply_final_true_peak_protection(
            raw_master, out_master, ffmpeg_path=ffmpeg, ws=ws
        )
    except RuntimeError as exc:
        error = str(exc)
        if not out_master.is_file():
            shutil.copy2(raw_master, out_master)

    wav_loud = measure_loudness(out_master, ffmpeg=ffmpeg)
    wav_peak = wav_loud.get("dbtp")
    if wav_peak is None:
        wav_peak = _measure_true_peak_dbtp(out_master, ffmpeg_path=ffmpeg)
    aac_file = out_master.with_suffix(".delivery_check.m4a")
    aac_loud: dict[str, Any] = {}
    try:
        _encode_aac_persistent(out_master, aac_file, ffmpeg=ffmpeg)
        aac_loud = measure_loudness(aac_file, ffmpeg=ffmpeg)
    finally:
        aac_file.unlink(missing_ok=True)
    aac_peak = aac_loud.get("dbtp")
    if aac_peak is None:
        aac_peak = reported_aac

    ceilings = evaluate_tp_ceilings(wav_peak, aac_peak, ws=ws)
    return {
        "tp_gain_db": float(gain),
        "tp_reported_aac_dbtp": reported_aac,
        "tp_wall_s": round(time.monotonic() - t0, 3),
        "tp_guard_error": error,
        "tp_guard_production_completed": error is None,
        "verification_wav": wav_loud,
        "verification_aac": aac_loud,
        **ceilings,
        "guard_verdict": (
            "fail"
            if error or not ceilings["pass_production_rule"]
            else ("strict_aac_miss" if not ceilings["pass_strict_aac"] else "production_ok")
        ),
    }


def apply_constant_gain_db(
    src: Path,
    dest: Path,
    gain_db: float,
    *,
    segment_start_s: float | None = None,
    segment_duration_s: float | None = None,
    ffmpeg: str = "ffmpeg",
) -> None:
    """Linear gain only (no loudnorm). Optionally trim with -ss/-t."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if segment_start_s is not None and segment_start_s > 0:
        cmd.extend(["-ss", f"{segment_start_s:.6f}"])
    cmd.extend(["-i", str(src)])
    if segment_duration_s is not None:
        cmd.extend(["-t", f"{segment_duration_s:.6f}"])
    cmd.extend(
        [
            "-af",
            f"volume={gain_db:.6f}dB",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ]
    )
    import subprocess

    subprocess.run(cmd, check=True)
