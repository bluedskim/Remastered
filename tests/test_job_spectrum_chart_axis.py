"""Mirrors spectrum chart Y-axis logic in job-spectrum.js (keep in sync)."""

from __future__ import annotations

import math

Y_FLOOR_DB = -42


def ceil_to_step(value: float, step: float) -> float:
    return math.ceil(value / step) * step


def compute_y_axis_range(all_db: list[float]) -> tuple[float, float]:
    data_max = max(all_db)
    y_min = float(Y_FLOOR_DB)
    headroom = 3.0
    raw_top = data_max + headroom
    round_step = 3.0 if raw_top <= 12 else 6.0
    y_max = ceil_to_step(raw_top, round_step)
    min_span = 24.0
    if y_max - y_min < min_span:
        y_max = y_min + min_span
    return y_min, y_max


def choose_y_tick_step(y_min: float, y_max: float) -> int:
    span = y_max - y_min
    best_step = 6
    best_score = math.inf
    for step in (10, 6):
        count = sum(1 for g in _frange(y_min, y_max, step))
        if 5 <= count <= 7:
            score = abs(count - 6)
            if score < best_score:
                best_score = score
                best_step = step
    if best_score is math.inf:
        return 10 if span > 48 else 6
    return best_step


def _frange(start: float, stop: float, step: float) -> list[float]:
    out: list[float] = []
    g = start
    while g <= stop + 1e-6:
        out.append(g)
        g += step
    return out


def strong_bass_steep_rolloff_series() -> list[float]:
    """Synthetic relative dB shape: strong bass, steep air rolloff (deep negatives)."""
    out: list[float] = []
    for i in range(100):
        t = i / 99
        hz = 20 * (20000 / 20) ** t
        orig = 8 * math.exp(-((math.log10(hz) - math.log10(80)) / 0.55) ** 2)
        rolloff = -max(0.0, math.log10(hz) - math.log10(900)) * 34
        out.append(orig + rolloff)
    return out


def test_y_floor_is_minus_42() -> None:
    deep = [-72.0, -58.0, -12.0, 6.0]
    y_min, y_max = compute_y_axis_range(deep)
    assert y_min == -42.0
    assert y_max >= 9.0
    assert y_max <= 18.0


def test_tick_steps_yield_five_to_seven_labels() -> None:
    curves = strong_bass_steep_rolloff_series()
    y_min, y_max = compute_y_axis_range(curves)
    step = choose_y_tick_step(y_min, y_max)
    assert step in (6, 10)
    count = len(_frange(y_min, y_max, step))
    assert 5 <= count <= 7


def test_axis_not_stretched_by_deep_data_min() -> None:
    """Old behavior would extend yMin below -60; new caps display at -42."""
    curves = strong_bass_steep_rolloff_series()
    y_min, _ = compute_y_axis_range(curves)
    assert y_min == -42.0
    assert min(curves) < -40