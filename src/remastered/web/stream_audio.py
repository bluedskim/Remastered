from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

PLAYBACK_SUFFIX = ".playback.m4a"


def playback_path_for_wav(wav_path: Path) -> Path:
    return wav_path.with_name(wav_path.stem + PLAYBACK_SUFFIX)


def transcode_wav_for_playback(
    wav_path: Path,
    *,
    extra_gain_db: float = 0.0,
    ffmpeg_path: str = "ffmpeg",
) -> Path:
    """AAC (192k) in M4A for browser streaming; idempotent if WAV unchanged."""
    wav_path = wav_path.resolve()
    out = playback_path_for_wav(wav_path)
    if extra_gain_db >= -1e-9 and out.is_file():
        try:
            if out.stat().st_mtime >= wav_path.stat().st_mtime:
                return out
        except OSError:
            pass
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(wav_path),
    ]
    if extra_gain_db < -1e-9:
        cmd.extend(["-af", f"volume={extra_gain_db:.6f}dB"])
    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info("Playback transcode: %s -> %s", wav_path.name, out.name)
    return out
