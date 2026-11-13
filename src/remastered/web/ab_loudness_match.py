"""Playback-only A/B loudness match helpers (attenuate-only gain strategy).

The browser ab-player mirrors ``playback_attenuation_gains_db`` when applying match.
"""

from __future__ import annotations

import math


def playback_attenuation_gains_db(
    lufs_original: float,
    lufs_remastered: float,
) -> tuple[float, float]:
    """Per-side playback attenuation in dB (never positive) to match integrated loudness."""
    if not (math.isfinite(lufs_original) and math.isfinite(lufs_remastered)):
        return (0.0, 0.0)
    target = min(lufs_original, lufs_remastered)
    orig_db = target - lufs_original
    rem_db = target - lufs_remastered
    return (min(orig_db, 0.0), min(rem_db, 0.0))


def attenuation_db_to_linear(db: float) -> float:
    if not math.isfinite(db) or db >= 0.0:
        return 1.0
    return 10.0 ** (db / 20.0)


def attenuation_db_to_volume_percent(db: float) -> int | None:
    """Whole-percent playback volume ratio for attenuation ``db`` (matches ``attenuation_db_to_linear``)."""
    if not math.isfinite(db) or db >= -0.05:
        return None
    return round(attenuation_db_to_linear(db) * 100.0)


def playback_attenuation_gains_linear(
    lufs_original: float,
    lufs_remastered: float,
) -> tuple[float, float]:
    orig_db, rem_db = playback_attenuation_gains_db(lufs_original, lufs_remastered)
    return (
        attenuation_db_to_linear(orig_db),
        attenuation_db_to_linear(rem_db),
    )


def remaster_volume_percent_of_original(
    lufs_mastered_wav: float,
    lufs_original: float,
) -> int | None:
    """Whole percent: remastered WAV loudness relative to original source (100 = same level)."""
    if not (math.isfinite(lufs_mastered_wav) and math.isfinite(lufs_original)):
        return None
    ratio = 10.0 ** ((lufs_mastered_wav - lufs_original) / 20.0)
    return round(ratio * 100.0)


def format_remaster_volume_of_original_line(percent: int | None) -> str | None:
    if percent is None:
        return None
    return f"Remastered is {percent}% of original volume"


def format_playback_attenuation_indicator(side: str, attenuation_db: float) -> str | None:
    """Compact UI label for playback-only loudness-match attenuation on ``side``."""
    if side not in ("original", "remastered"):
        return None
    percent = attenuation_db_to_volume_percent(attenuation_db)
    if percent is None:
        return None
    label = "Original" if side == "original" else "Remastered"
    return f"{label} {percent}% volume"
