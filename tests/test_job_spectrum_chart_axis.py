"""Mirrors spectrum chart Y-axis logic in job-spectrum.js (keep in sync)."""

from __future__ import annotations

import math

CLIP_TARGET_DB = -42


def ceil_to_step(value: float, step: float) -> float:
    return math.ceil(value / step) * step


def display_floor_for_step(step: int) -> float:
    return -42.0 if step == 6 else -40.0


def tick_count(y_min: float, y_max: float, tick_step: int) -> int:
    return int((y_max - y_min) / tick_step) + 1


def compute_y_axis_range(all_db: list[float]) -> tuple[float, float, int, int]:
    data_max = max(all_db)
    headroom = 3.0
    required_top = data_max + headroom
    min_ticks = 5
    max_ticks = 7
    for tick_step in (10, 20, 6):
        y_min = display_floor_for_step(tick_step)
        y_max = ceil_to_step(required_top, float(tick_step))
        count = tick_count(y_min, y_max, tick_step)
        if count > max_ticks:
            label_every = 2
            label_count = math.ceil(count / label_every)
            if min_ticks <= label_count <= max_ticks:
                return y_min, y_max, tick_step, label_every
            continue
        while count < min_ticks:
            y_max += tick_step
            count += 1
        return y_min, y_max, tick_step, 1
    tick_step = 20
    y_min = display_floor_for_step(tick_step)
    y_max = ceil_to_step(required_top, float(tick_step))
    return y_min, y_max, tick_step, 1


def y_axis_tick_values(y_min: float, y_max: float, tick_step: int) -> list[float]:
    k0 = int(y_min // tick_step)
    k1 = int(y_max // tick_step)
    return [float(k * tick_step) for k in range(k0, k1 + 1)]


def strong_bass_steep_rolloff_series() -> list[float]:
    """Synthetic relative dB shape: strong bass, steep air rolloff (deep negatives)."""
    out: list[float] = []
    for i in range(100):
        t = i / 99
        hz = 20 * (20000 / 20) ** t
        orig = 8 * math.exp(-((math.log10(hz) - math.log10(80)) / 0.55) ** 2)
        rolloff = -max(0.0, math.log10(hz) - math.log10(700)) * 40
        out.append(orig + rolloff)
    return out


def job_18ff496f_like_series() -> list[float]:
    """~+26 dB bass bump near 80 Hz with steep top-end rolloff (mixed curves)."""
    out: list[float] = []
    for i in range(100):
        t = i / 99
        hz = 20 * (20000 / 20) ** t
        bass = 26 * math.exp(-((math.log(hz) - math.log(80)) / 0.35) ** 2)
        if hz >= 10_000:
            air = -22 - max(0.0, math.log10(hz) - 4.0) * 120
        else:
            air = -0.4 * max(0.0, math.log10(hz) - 2.5)
        out.append(bass + air)
    return out


def test_y_axis_uses_step10_with_minus_40_floor() -> None:
    deep = [-72.0, -58.0, -12.0, 6.0]
    y_min, y_max, step, _ = compute_y_axis_range(deep)
    assert step == 10
    assert y_min == -40.0
    assert y_max % 10 == 0


def test_ticks_are_zero_anchored_multiples() -> None:
    curves = strong_bass_steep_rolloff_series()
    y_min, y_max, step, label_every = compute_y_axis_range(curves)
    ticks = y_axis_tick_values(y_min, y_max, step)
    labeled = ticks[::label_every]
    assert 5 <= len(labeled) <= 7
    for g in ticks:
        assert abs(g / step - round(g / step)) < 1e-9
    assert 0.0 in ticks or (y_min <= 0 <= y_max)


def test_axis_floor_minus_40_on_10_db_grid() -> None:
    curves = strong_bass_steep_rolloff_series()
    y_min, _, step, _ = compute_y_axis_range(curves)
    assert step in (6, 10, 20)
    if step == 10:
        assert y_min == -40.0
    elif step == 6:
        assert y_min == -42.0
    else:
        assert y_min == -40.0
    assert min(curves) < CLIP_TARGET_DB - 5


def test_high_bass_peak_26_db_never_clips_top() -> None:
    """Regression for job 18ff496f: yMax must cover +26 dB peak."""
    curves = job_18ff496f_like_series()
    all_db = curves + [c - 1 for c in curves] + [c - 2 for c in curves]
    peak = max(all_db)
    assert peak >= 25.0
    y_min, y_max, step, _ = compute_y_axis_range(all_db)
    assert y_max >= ceil_to_step(peak + 3.0, float(step))
    assert y_max > peak
    assert step in (10, 20)