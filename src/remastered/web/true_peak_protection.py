"""Final true-peak ceiling using constant gain only (no limiter/EQ/loudnorm)."""

from __future__ import annotations

import logging
import math
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from remastered.web.settings import WebSettings

logger = logging.getLogger(__name__)

_TRUE_PEAK_SUMMARY_RE = re.compile(
    r"True peak:\s*\n\s*Peak:\s*([+-]?\d+(?:\.\d+)?)\s*dBFS",
    re.IGNORECASE | re.MULTILINE,
)
# Frame lines: TPK:  -1.2  -1.1 dBFS  (take max of numeric tokens before dBFS)
_TPK_LINE_RE = re.compile(
    r"\bTPK:\s*((?:[+-]?\d+(?:\.\d+)?\s*)+)dBFS",
    re.IGNORECASE,
)


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
    m = _TRUE_PEAK_SUMMARY_RE.search(stderr)
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


def apply_final_true_peak_protection(
    raw_mastered_wav: Path,
    output_mastered_wav: Path,
    *,
    log: Callable[[str], None] | None = None,
    ffmpeg_path: str = "ffmpeg",
    ws: WebSettings | None = None,
) -> tuple[float, float]:
    """Attenuate with constant gain from ``raw_mastered_wav`` until checks pass.

    Every write to ``output_mastered_wav`` is derived from ``raw_mastered_wav`` with
    ``volume=dB`` (no boost — total gain <= 0). Measures true peak on WAV and on AAC
    decoded the same way as browser playback (192k). Bounded attempts.
    """
    ws = ws or WebSettings()
    ceiling = float(ws.true_peak_ceiling_dbtp)
    headroom = float(ws.true_peak_pass_headroom_db)
    max_attempts = int(ws.true_peak_max_attempts)
    min_step = float(ws.true_peak_min_step_db)

    def _lg(msg: str) -> None:
        if log:
            log(msg)
        logger.info(msg)

    peak_raw = _measure_true_peak_dbtp(raw_mastered_wav, ffmpeg_path=ffmpeg_path)
    if peak_raw is None:
        raise RuntimeError("Could not measure true peak of mastered WAV.")

    def render_from_raw(total_db: float) -> None:
        if total_db >= -1e-9:
            import shutil

            shutil.copy2(raw_mastered_wav, output_mastered_wav)
            return
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            _apply_gain_wav(
                raw_mastered_wav,
                tmp_path,
                total_db,
                ffmpeg_path=ffmpeg_path,
            )
            tmp_path.replace(output_mastered_wav)
        finally:
            tmp_path.unlink(missing_ok=True)

    total_gain = 0.0
    if peak_raw > ceiling:
        total_gain = min(0.0, ceiling - peak_raw)
    render_from_raw(total_gain)

    for attempt in range(1, max_attempts + 1):
        peak_wav = _measure_true_peak_dbtp(output_mastered_wav, ffmpeg_path=ffmpeg_path)
        if peak_wav is None:
            raise RuntimeError("True peak measurement failed (WAV).")

        with tempfile.TemporaryDirectory() as td:
            m4a = Path(td) / "check.m4a"
            _encode_playback_aac(output_mastered_wav, m4a, ffmpeg_path=ffmpeg_path)
            peak_aac = _measure_true_peak_dbtp(m4a, ffmpeg_path=ffmpeg_path)
        if peak_aac is None:
            raise RuntimeError("True peak measurement failed (AAC).")

        if peak_wav <= ceiling and peak_aac <= ceiling + headroom:
            _lg(
                f"True-peak protection: total gain {total_gain:.3f} dB; "
                f"WAV {peak_wav:.2f} dBTP, AAC decode {peak_aac:.2f} dBTP (ceiling {ceiling:.2f} dBTP)."
            )
            return total_gain, peak_aac

        # Need more attenuation (WAV over ceiling and/or AAC overshoot)
        wav_over = max(0.0, peak_wav - ceiling)
        aac_over = max(0.0, peak_aac - ceiling - headroom)
        step = max(wav_over, aac_over, min_step)
        total_gain -= step
        render_from_raw(total_gain)

    raise RuntimeError(
        f"True-peak protection failed after {max_attempts} attempts "
        f"(ceiling {ceiling:.2f} dBTP, last gain {total_gain:.3f} dB)."
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
    import shutil

    raw = raw_mastered_path_for_final(final_mastered_wav)
    shutil.copy2(final_mastered_wav, raw)
    return raw
