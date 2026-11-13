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


def _measure_aac_peak_from_wav(
    guarded_wav: Path,
    *,
    extra_gain_db: float = 0.0,
    ffmpeg_path: str = "ffmpeg",
) -> float:
    """Encode playback AAC from ``guarded_wav`` (optionally further attenuated) and measure TP."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        encode_src = guarded_wav
        if extra_gain_db < -1e-9:
            encode_src = td_path / "preview_src.wav"
            _apply_gain_wav(
                guarded_wav,
                encode_src,
                extra_gain_db,
                ffmpeg_path=ffmpeg_path,
            )
        m4a = td_path / "check.m4a"
        _encode_playback_aac(encode_src, m4a, ffmpeg_path=ffmpeg_path)
        peak_aac = _measure_true_peak_dbtp(m4a, ffmpeg_path=ffmpeg_path)
    if peak_aac is None:
        raise RuntimeError("True peak measurement failed (AAC).")
    return peak_aac


def _protect_wav_only(
    raw_mastered_wav: Path,
    output_mastered_wav: Path,
    *,
    ws: WebSettings,
    ffmpeg_path: str,
) -> tuple[float, float, float]:
    """Constant-gain WAV protection only. Returns (adopted_gain, peak_wav, phase1_pass_gain)."""
    ceiling = float(ws.true_peak_ceiling_dbtp)
    pass_ceiling = pass_ceiling_dbtp(ws)
    max_attempts = int(ws.true_peak_max_attempts)
    min_step = float(ws.true_peak_min_step_db)
    refine_step = float(ws.true_peak_refine_step_db)
    refine_max = int(ws.true_peak_refine_max_attempts)

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

    total_gain = 0.0
    if peak_raw > ceiling:
        total_gain = min(0.0, ceiling - peak_raw)
    g0 = total_gain
    render_from_raw(total_gain)

    last_wav: float | None = None
    phase1_pass_gain: float | None = None
    phase1_peak_wav: float | None = None
    phase1_attempts = 0

    for attempt in range(1, max_attempts + 1):
        phase1_attempts = attempt
        peak_wav = _measure_true_peak_dbtp(
            output_mastered_wav, ffmpeg_path=ffmpeg_path
        )
        if peak_wav is None:
            raise RuntimeError("True peak measurement failed (WAV).")
        last_wav = peak_wav

        if peak_wav <= pass_ceiling:
            phase1_pass_gain = total_gain
            phase1_peak_wav = peak_wav
            break

        wav_over = max(0.0, peak_wav - pass_ceiling)
        step = max(wav_over, min_step)
        total_gain -= step
        render_from_raw(total_gain)
    else:
        raise RuntimeError(
            f"True-peak WAV protection failed after {max_attempts} attempts "
            f"(pass <= {pass_ceiling:.2f} dBTP, last WAV {last_wav:.2f}, "
            f"gain {total_gain:.3f} dB)."
        )

    assert phase1_pass_gain is not None
    g_pass = phase1_pass_gain
    adopted_gain = g_pass
    peak_wav = phase1_peak_wav
    assert peak_wav is not None

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
            pw = _measure_true_peak_dbtp(
                output_mastered_wav, ffmpeg_path=ffmpeg_path
            )
            if pw is None:
                raise RuntimeError("True peak measurement failed (WAV).")
            if pw <= pass_ceiling:
                adopted_gain = cand
                peak_wav = pw
                break
        else:
            render_from_raw(g_pass)
            peak_wav = phase1_peak_wav

    if abs(adopted_gain - g_pass) > 1e-9:
        render_from_raw(adopted_gain)
        pw = _measure_true_peak_dbtp(output_mastered_wav, ffmpeg_path=ffmpeg_path)
        if pw is None:
            raise RuntimeError("True peak measurement failed (WAV).")
        if pw <= pass_ceiling:
            peak_wav = pw
        else:
            adopted_gain = g_pass
            render_from_raw(g_pass)
            pw = _measure_true_peak_dbtp(
                output_mastered_wav, ffmpeg_path=ffmpeg_path
            )
            if pw is None or pw > pass_ceiling:
                raise RuntimeError(
                    "True-peak protection could not verify the phase-1 pass gain."
                )
            peak_wav = pw

    assert peak_wav <= pass_ceiling
    return adopted_gain, peak_wav, g_pass


def _find_preview_extra_gain(
    guarded_wav: Path,
    *,
    ws: WebSettings,
    ffmpeg_path: str,
) -> tuple[float, float]:
    """Extra attenuation on guarded WAV for AAC preview only. Returns (extra_db, peak_aac)."""
    pass_ceiling = pass_ceiling_dbtp(ws)
    max_attempts = int(ws.true_peak_max_attempts)
    min_step = float(ws.true_peak_min_step_db)

    extra = 0.0
    last_aac: float | None = None
    peak_aac: float | None = None

    for attempt in range(1, max_attempts + 1):
        peak_aac = _measure_aac_peak_from_wav(
            guarded_wav,
            extra_gain_db=extra,
            ffmpeg_path=ffmpeg_path,
        )
        last_aac = peak_aac
        if peak_aac <= pass_ceiling:
            return extra, peak_aac

        aac_over = max(0.0, peak_aac - pass_ceiling)
        step = max(aac_over, min_step)
        extra -= step

    raise RuntimeError(
        f"True-peak preview protection failed after {max_attempts} attempts "
        f"(pass <= {pass_ceiling:.2f} dBTP, last AAC {last_aac:.2f}, "
        f"preview extra {extra:.3f} dB)."
    )


def apply_final_true_peak_protection(
    raw_mastered_wav: Path,
    output_mastered_wav: Path,
    *,
    log: Callable[[str], None] | None = None,
    ffmpeg_path: str = "ffmpeg",
    ws: WebSettings | None = None,
) -> tuple[float, float, float, float]:
    """Attenuate mastered WAV and plan preview-only extra gain (separate constants).

    WAV output is derived from ``raw_mastered_wav`` with the minimal constant gain so
    its true peak is <= pass ceiling. Browser preview AAC is encoded separately with
    optional additional constant attenuation on top of that WAV (never applied to the
    saved file).

    Returns ``(wav_gain_db, peak_wav_dbtp, peak_aac_dbtp, preview_extra_gain_db)``.
    ``preview_extra_gain_db`` is <= 0 and is passed to playback transcode. On failure
    restores output before raising.
    """
    ws = ws or WebSettings()
    pass_ceiling = pass_ceiling_dbtp(ws)
    ceiling = float(ws.true_peak_ceiling_dbtp)

    def _lg(msg: str) -> None:
        if log:
            log(msg)
        logger.info(msg)

    def _restore_output() -> None:
        shutil.copy2(raw_mastered_wav, output_mastered_wav)

    try:
        wav_gain, peak_wav, _g_pass = _protect_wav_only(
            raw_mastered_wav,
            output_mastered_wav,
            ws=ws,
            ffmpeg_path=ffmpeg_path,
        )

        preview_extra, peak_aac = _find_preview_extra_gain(
            output_mastered_wav,
            ws=ws,
            ffmpeg_path=ffmpeg_path,
        )

        msg = (
            f"True-peak protection: WAV gain {wav_gain:.3f} dB "
            f"({peak_wav:.2f} dBTP"
        )
        if preview_extra < -1e-9:
            msg += (
                f"); preview extra {preview_extra:.3f} dB "
                f"(AAC decode {peak_aac:.2f} dBTP"
            )
        else:
            msg += f"; AAC decode {peak_aac:.2f} dBTP"
        msg += (
            f"; pass <= {pass_ceiling:.2f} dBTP, target {ceiling:.2f} dBTP)."
        )
        _lg(msg)
        return wav_gain, peak_wav, peak_aac, preview_extra
    except Exception:
        _restore_output()
        raise


def verify_delivered_playback_peak(
    mastered_wav: Path,
    playback_m4a: Path,
    *,
    preview_extra_gain_db: float,
    ffmpeg_path: str = "ffmpeg",
    ws: WebSettings | None = None,
) -> float:
    """Re-encode preview with more attenuation if delivered M4A true peak is over ceiling.

    Returns the ``preview_extra_gain_db`` used after any correction (<= input).
    Does not modify ``mastered_wav``.
    """
    from remastered.web.stream_audio import transcode_wav_for_playback

    ws = ws or WebSettings()
    pass_ceiling = pass_ceiling_dbtp(ws)
    peak = _measure_true_peak_dbtp(playback_m4a, ffmpeg_path=ffmpeg_path)
    if peak is None:
        raise RuntimeError("True peak measurement failed (delivered playback).")
    if peak <= pass_ceiling:
        return preview_extra_gain_db

    extra = preview_extra_gain_db
    min_step = float(ws.true_peak_min_step_db)
    max_attempts = int(ws.true_peak_max_attempts)
    last_peak: float | None = peak

    for _ in range(max_attempts):
        over = max(0.0, last_peak - pass_ceiling) if last_peak is not None else min_step
        step = max(over, min_step)
        extra -= step
        refreshed = transcode_wav_for_playback(
            mastered_wav,
            extra_gain_db=extra,
            ffmpeg_path=ffmpeg_path,
        )
        peak_delivered = _measure_true_peak_dbtp(refreshed, ffmpeg_path=ffmpeg_path)
        if peak_delivered is None:
            raise RuntimeError("True peak measurement failed (delivered playback).")
        last_peak = peak_delivered
        if peak_delivered <= pass_ceiling:
            return extra

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
