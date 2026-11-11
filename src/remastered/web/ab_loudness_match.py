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


def playback_attenuation_gains_linear(
    lufs_original: float,
    lufs_remastered: float,
) -> tuple[float, float]:
    orig_db, rem_db = playback_attenuation_gains_db(lufs_original, lufs_remastered)
    return (
        attenuation_db_to_linear(orig_db),
        attenuation_db_to_linear(rem_db),
    )


def format_playback_attenuation_indicator(side: str, attenuation_db: float) -> str | None:
    """Compact UI label for playback-only loudness-match attenuation on ``side``."""
    if side not in ("original", "remastered"):
        return None
    if not math.isfinite(attenuation_db) or attenuation_db >= -0.05:
        return None
    label = "Original" if side == "original" else "Remastered"
    return f"{label} \u2212{abs(attenuation_db):.1f} dB"
