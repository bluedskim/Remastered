"""Integrated loudness (LUFS) measurement for delivered WAV files via ffmpeg ebur128."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_SUMMARY_MARK = "Summary:"
_INTEGRATED_LUFS_RE = re.compile(
    r"Integrated loudness:\s*\n\s*I:\s*([+-]?\d+(?:\.\d+)?)\s*LUFS",
    re.IGNORECASE | re.MULTILINE,
)
_INTEGRATED_LUFS_FALLBACK_RE = re.compile(
    r"\bI:\s*([+-]?\d+(?:\.\d+)?)\s*LUFS",
    re.IGNORECASE,
)


def _ebur128_summary(stderr: str) -> str:
    idx = stderr.rfind(_SUMMARY_MARK)
    return stderr[idx:] if idx >= 0 else ""


def parse_integrated_lufs_from_ebur128_stderr(stderr: str) -> float | None:
    """Parse integrated loudness (I) from ffmpeg ``ebur128`` filter stderr."""
    block = _ebur128_summary(stderr)
    search_in = block if block else stderr
    m = _INTEGRATED_LUFS_RE.search(search_in)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    matches = list(_INTEGRATED_LUFS_FALLBACK_RE.finditer(search_in))
    if not matches:
        return None
    try:
        return float(matches[-1].group(1))
    except ValueError:
        return None


def measure_integrated_lufs(path: Path, *, ffmpeg_path: str = "ffmpeg") -> float | None:
    """Return integrated loudness in LUFS for ``path``, or None if measurement fails."""
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-af",
        "ebur128",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    stderr = proc.stderr or ""
    if proc.returncode != 0 and "I:" not in stderr:
        return None
    return parse_integrated_lufs_from_ebur128_stderr(stderr)
