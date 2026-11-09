"""Final true-peak ceiling using constant gain only (no limiter/EQ/loudnorm)."""

from __future__ import annotations

import errno
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from remastered.web.settings import WebSettings

logger = logging.getLogger(__name__)

_SUMMARY_MARK = "Summary:"
_TRUE_PEAK_SUMMARY_RE = re.compile(
    r"True peak:\s*\n\s*Peak:\s*([+-]?\d+(?:\.\d+)?)\s*dBFS",
    re.IGNORECASE | re.MULTILINE,
)
_TPK_LINE_RE = re.compile(
    r"\bTPK:\s*((?:[+-]?\d+(?:\.\d+)?\s*)+)dBFS",
    re.IGNORECASE,
)


def pass_ceiling_dbtp(ws: WebSettings) -> float:
    """Measured WAV and AAC true peaks must be <= this level to pass."""
    margin = max(0.0, float(ws.true_peak_pass_headroom_db))
    return float(ws.true_peak_ceiling_dbtp) - margin


def _ebur128_summary(stderr: str) -> str:
    idx = stderr.rfind(_SUMMARY_MARK)
    return stderr[idx:] if idx >= 0 else ""


def _measure_true_peak_dbtp(path: Path, *, ffmpeg_path: str = "ffmpeg") -> float | None:
    """Return true peak in dBTP relative to full scale (negative = headroom), or None."""
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-af",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stderr = proc.stderr or ""
    block = _ebur128_summary(stderr)
    search_in = block if block else stderr
    m = _TRUE_PEAK_SUMMARY_RE.search(search_in)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    peaks: list[float] = []
    for line in stderr.splitlines():
        mm = _TPK_LINE_RE.search(line)
        if mm:
            chunk = mm.group(1)
            for tok in chunk.split():
                try:
                    peaks.append(float(tok))
                except ValueError:
                    continue
    if not peaks:
        return None
    return max(peaks)


def _apply_gain_wav(
    src: Path,
    dest: Path,
    gain_db: float,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    if "24" in src.stem.lower():
        codec = "pcm_s24le"
    else:
        codec = "pcm_s16le"
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-af",
        f"volume={gain_db:.6f}dB",
        "-c:a",
        codec,
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _encode_playback_aac(wav: Path, m4a: Path, *, ffmpeg_path: str = "ffmpeg") -> None:
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(wav),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(m4a),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _commit_staging_file(staging: Path, dest: Path) -> None:
    """Move ``staging`` onto ``dest``, same filesystem when possible (Docker /tmp vs /work)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staging, dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copy2(staging, dest)
        staging.unlink(missing_ok=True)


def _write_attenuated_master(
    raw_mastered_wav: Path,
    output_mastered_wav: Path,
    total_db: float,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    """Write gain-attenuated audio to ``output_mastered_wav`` without cross-volume rename."""
    if total_db >= -1e-9:
        shutil.copy2(raw_mastered_wav, output_mastered_wav)
        return

    out_dir = output_mastered_wav.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    staging: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            prefix=".tp_stage_",
            dir=str(out_dir),
            delete=False,
        ) as tmp:
            staging = Path(tmp.name)
        _apply_gain_wav(
            raw_mastered_wav,
            staging,
            total_db,
            ffmpeg_path=ffmpeg_path,
        )
        _commit_staging_file(staging, output_mastered_wav)
        staging = None
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def _measure_wav_and_aac_peaks(
    wav_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> tuple[float, float]:
    peak_wav = _measure_true_peak_dbtp(wav_path, ffmpeg_path=ffmpeg_path)
    if peak_wav is None:
        raise RuntimeError("True peak measurement failed (WAV).")
    with tempfile.TemporaryDirectory() as td:
        m4a = Path(td) / "check.m4a"
        _encode_playback_aac(wav_path, m4a, ffmpeg_path=ffmpeg_path)
        peak_aac = _measure_true_peak_dbtp(m4a, ffmpeg_path=ffmpeg_path)
    if peak_aac is None:
        raise RuntimeError("True peak measurement failed (AAC).")
    return peak_wav, peak_aac


def apply_final_true_peak_protection(
    raw_mastered_wav: Path,
    output_mastered_wav: Path,
    *,
    log: Callable[[str], None] | None = None,
    ffmpeg_path: str = "ffmpeg",
    ws: WebSettings | None = None,
) -> tuple[float, float, float, float]:
    """Attenuate with constant gain from ``raw_mastered_wav`` until checks pass.

    Every write to ``output_mastered_wav`` is derived from ``raw_mastered_wav`` with
    ``volume=dB`` (no boost — total gain <= 0). Measures true peak on WAV and on AAC
    decoded the same way as browser playback (192k). Bounded attempts.

    Returns ``(adopted_gain_db, peak_wav_dbtp, peak_aac_dbtp, phase1_pass_gain_db)``.
    ``phase1_pass_gain_db`` is the first passing gain from the downward search (worker
    fallback if delivered playback overshoots). On failure restores output before raising.
    """
    ws = ws or WebSettings()
    ceiling = float(ws.true_peak_ceiling_dbtp)
    pass_ceiling = pass_ceiling_dbtp(ws)
    max_attempts = int(ws.true_peak_max_attempts)
    min_step = float(ws.true_peak_min_step_db)
    refine_step = float(ws.true_peak_refine_step_db)
    refine_max = int(ws.true_peak_refine_max_attempts)

    def _lg(msg: str) -> None:
        if log:
            log(msg)
        logger.info(msg)

    peak_raw = _measure_true_peak_dbtp(raw_mastered_wav, ffmpeg_path=ffmpeg_path)
    if peak_raw is None:
        raise RuntimeError("Could not measure true peak of mastered WAV.")

    def render_from_raw(total_db: float) -> None:
        _write_attenuated_master(
            raw_mastered_wav,
            output_mastered_wav,
            total_db,
            ffmpeg_path=ffmpeg_path,
        )

    def _restore_output() -> None:
        shutil.copy2(raw_mastered_wav, output_mastered_wav)

    def measure_rendered() -> tuple[float, float]:
        return _measure_wav_and_aac_peaks(
            output_mastered_wav, ffmpeg_path=ffmpeg_path
        )

    total_gain = 0.0
    if peak_raw > ceiling:
        total_gain = min(0.0, ceiling - peak_raw)
    g0 = total_gain
    render_from_raw(total_gain)

    last_wav: float | None = None
    last_aac: float | None = None
    phase1_pass_gain: float | None = None
    phase1_peak_wav: float | None = None
    phase1_peak_aac: float | None = None
    phase1_attempts = 0
    try:
        for attempt in range(1, max_attempts + 1):
            phase1_attempts = attempt
            peak_wav, peak_aac = measure_rendered()
            last_wav = peak_wav
            last_aac = peak_aac

            if peak_wav <= pass_ceiling and peak_aac <= pass_ceiling:
                phase1_pass_gain = total_gain
                phase1_peak_wav = peak_wav
                phase1_peak_aac = peak_aac
                break

            wav_over = max(0.0, peak_wav - pass_ceiling)
            aac_over = max(0.0, peak_aac - pass_ceiling)
            step = max(wav_over, aac_over, min_step)
            total_gain -= step
            render_from_raw(total_gain)
        else:
            raise RuntimeError(
                f"True-peak protection failed after {max_attempts} attempts "
                f"(pass <= {pass_ceiling:.2f} dBTP, last WAV {last_wav:.2f}, "
                f"last AAC {last_aac:.2f}, gain {total_gain:.3f} dB)."
            )

        assert phase1_pass_gain is not None
        g_pass = phase1_pass_gain
        adopted_gain = g_pass
        peak_wav = phase1_peak_wav
        peak_aac = phase1_peak_aac
        refined_from: float | None = None
        phase2_measured_gain: float | None = None

        if (
            refine_max > 0
            and phase1_attempts >= 2
            and refine_step > 0
            and g0 > g_pass + 1e-9
        ):
            candidates: list[float] = []
            k = 1
            while len(candidates) < refine_max:
                cand = g0 - k * refine_step
                if cand <= g_pass + 1e-9:
                    break
                candidates.append(min(0.0, cand))
                k += 1

            for cand in candidates:
                render_from_raw(cand)
                pw, pa = measure_rendered()
                if pw <= pass_ceiling and pa <= pass_ceiling:
                    adopted_gain = cand
                    peak_wav = pw
                    peak_aac = pa
                    refined_from = g_pass
                    phase2_measured_gain = cand
                    break
            else:
                render_from_raw(g_pass)
                peak_wav = phase1_peak_wav
                peak_aac = phase1_peak_aac
                phase2_measured_gain = g_pass

        if abs(adopted_gain - g_pass) > 1e-9:
            already_verified = (
                phase2_measured_gain is not None
                and abs(adopted_gain - phase2_measured_gain) < 1e-9
            )
            if not already_verified:
                render_from_raw(adopted_gain)
                pw, pa = measure_rendered()
                if pw <= pass_ceiling and pa <= pass_ceiling:
                    peak_wav, peak_aac = pw, pa
                else:
                    adopted_gain = g_pass
                    render_from_raw(g_pass)
                    pw, pa = measure_rendered()
                    if pw > pass_ceiling or pa > pass_ceiling:
                        raise RuntimeError(
                            "True-peak protection could not verify the phase-1 pass gain."
                        )
                    peak_wav, peak_aac = pw, pa
                    refined_from = None

        assert peak_wav is not None and peak_aac is not None
        assert adopted_gain <= 0.0 + 1e-9
        assert peak_wav <= pass_ceiling and peak_aac <= pass_ceiling

        msg = (
            f"True-peak protection: total gain {adopted_gain:.3f} dB; "
            f"WAV {peak_wav:.2f} dBTP, AAC decode {peak_aac:.2f} dBTP "
            f"(pass <= {pass_ceiling:.2f} dBTP, target {ceiling:.2f} dBTP)."
        )
        if refined_from is not None:
            msg += f" (refined from {refined_from:.3f} dB)."
        _lg(msg)
        return adopted_gain, peak_wav, peak_aac, g_pass
    except Exception:
        _restore_output()
        raise


def verify_delivered_playback_peak(
    mastered_wav: Path,
    playback_m4a: Path,
    *,
    phase1_pass_gain_db: float,
    ffmpeg_path: str = "ffmpeg",
    ws: WebSettings | None = None,
) -> None:
    """Re-render at ``phase1_pass_gain_db`` if the streamed M4A true peak is over ceiling."""
    from remastered.web.stream_audio import transcode_wav_for_playback

    ws = ws or WebSettings()
    pass_ceiling = pass_ceiling_dbtp(ws)
    peak = _measure_true_peak_dbtp(playback_m4a, ffmpeg_path=ffmpeg_path)
    if peak is None:
        raise RuntimeError("True peak measurement failed (delivered playback).")
    if peak <= pass_ceiling:
        return

    raw = raw_mastered_path_for_final(mastered_wav)
    if not raw.is_file():
        raise RuntimeError("True-peak playback verify failed: raw master missing.")
    _write_attenuated_master(
        raw,
        mastered_wav,
        phase1_pass_gain_db,
        ffmpeg_path=ffmpeg_path,
    )
    pw, pa = _measure_wav_and_aac_peaks(mastered_wav, ffmpeg_path=ffmpeg_path)
    if pw > pass_ceiling or pa > pass_ceiling:
        raise RuntimeError(
            "True-peak protection failed after playback transcode verification."
        )
    refreshed = transcode_wav_for_playback(mastered_wav)
    peak_delivered = _measure_true_peak_dbtp(refreshed, ffmpeg_path=ffmpeg_path)
    if peak_delivered is None:
        raise RuntimeError("True peak measurement failed (delivered playback).")
    if peak_delivered > pass_ceiling:
        raise RuntimeError(
            "True-peak protection failed after playback transcode verification."
        )


def raw_mastered_path_for_final(final_mastered_wav: Path) -> Path:
    """Path to the unattenuated matchering output alongside the final WAV name."""
    name = final_mastered_wav.name
    if name.startswith("mastered_"):
        return final_mastered_wav.with_name(
            name.replace("mastered_", "mastered_matchering_", 1)
        )
    return final_mastered_wav.with_name("mastered_matchering" + final_mastered_wav.suffix)


def copy_matchering_to_raw(final_mastered_wav: Path) -> Path:
    """Preserve matchering output before replacing final path."""
    raw = raw_mastered_path_for_final(final_mastered_wav)
    shutil.copy2(final_mastered_wav, raw)
    return raw
