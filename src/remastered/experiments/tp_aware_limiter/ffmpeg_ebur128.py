"""Parse ffmpeg ebur128 filter output (Summary block only)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


_SUMMARY_MARK = "Summary:"
_INTEGRATED_I_RE = re.compile(
    r"Integrated loudness:\s*\n\s*I:\s*([+-]?\d+(?:\.\d+)?)\s*LUFS",
    re.MULTILINE,
)
_LRA_RE = re.compile(r"Loudness range:\s*\n\s*LRA:\s*([+-]?\d+(?:\.\d+)?)\s*LU", re.MULTILINE)
_TRUE_PEAK_SUMMARY_RE = re.compile(
    r"True peak:\s*\n\s*Peak:\s*([+-]?\d+(?:\.\d+)?)\s*dBFS",
    re.IGNORECASE | re.MULTILINE,
)
_TPK_LINE_RE = re.compile(
    r"\bTPK:\s*((?:[+-]?\d+(?:\.\d+)?\s*)+)dBFS",
    re.IGNORECASE,
)


def summary_block(stderr: str) -> str:
    idx = stderr.rfind(_SUMMARY_MARK)
    if idx < 0:
        return ""
    return stderr[idx:]


def parse_integrated_lufs(stderr: str) -> float | None:
    block = summary_block(stderr)
    if not block:
        return None
    m = _INTEGRATED_I_RE.search(block)
    if not m:
        return None
    return float(m.group(1))


def parse_lra(stderr: str) -> float | None:
    block = summary_block(stderr)
    if not block:
        return None
    m = _LRA_RE.search(block)
    if not m:
        return None
    return float(m.group(1))


def parse_true_peak_dbtp(stderr: str) -> float | None:
    block = summary_block(stderr)
    m = _TRUE_PEAK_SUMMARY_RE.search(block) if block else None
    if m:
        return float(m.group(1))
    peaks: list[float] = []
    for line in stderr.splitlines():
        mm = _TPK_LINE_RE.search(line)
        if mm:
            for tok in mm.group(1).split():
                try:
                    peaks.append(float(tok))
                except ValueError:
                    continue
    return max(peaks) if peaks else None


def run_ebur128(path: Path, *, peak: bool = False, ffmpeg: str = "ffmpeg") -> tuple[str, int]:
    af = "ebur128=peak=true" if peak else "ebur128"
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            af,
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    return proc.stderr or "", proc.returncode


def measure_loudness(path: Path, *, ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    stderr, rc = run_ebur128(path, peak=True, ffmpeg=ffmpeg)
    out: dict[str, Any] = {
        "path": str(path),
        "ffmpeg_rc": rc,
        "lufs_i": parse_integrated_lufs(stderr),
        "lra": parse_lra(stderr),
        "dbtp": parse_true_peak_dbtp(stderr),
        "summary_present": bool(summary_block(stderr)),
        "stderr_tail": "\n".join(stderr.splitlines()[-25:]),
    }
    if not out["summary_present"]:
        out["parse_error"] = "missing_ebur128_summary"
    elif out["lufs_i"] is None:
        out["parse_error"] = "missing_integrated_lufs"
    return out
