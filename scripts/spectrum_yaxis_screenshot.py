#!/usr/bin/env python3
"""Before/after Y-axis screenshots for spectrum chart (synthetic bass + rolloff data)."""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from test_job_spectrum_chart_axis import (  # noqa: E402
    Y_FLOOR_DB,
    choose_y_tick_step,
    compute_y_axis_range,
    strong_bass_steep_rolloff_series,
)


def _synthetic_curves() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    f: list[float] = []
    o: list[float] = []
    r: list[float] = []
    m: list[float] = []
    base = strong_bass_steep_rolloff_series()
    for i, orig in enumerate(base):
        t = i / 99
        hz = 20 * (20000 / 20) ** t
        f.append(hz)
        o.append(orig)
        r.append(orig - 2.5 - 0.5 * max(0, math.log10(hz) - 3))
        m.append(orig + 1.2 * max(0, math.log10(hz) - 3.2))
    return np.asarray(f), np.asarray(o), np.asarray(r), np.asarray(m)


def _old_axis(o: np.ndarray, r: np.ndarray, m: np.ndarray) -> tuple[float, float, int]:
    allv = np.concatenate([o, r, m])
    y_min = math.floor(float(np.min(allv)) / 3) * 3 - 3
    y_max = math.ceil(float(np.max(allv)) / 3) * 3 + 3
    if y_max - y_min < 12:
        y_min -= 6
        y_max += 6
    return y_min, y_max, 6


def _plot(
    path: Path,
    freqs: np.ndarray,
    o: np.ndarray,
    r: np.ndarray,
    m: np.ndarray,
    y_min: float,
    y_max: float,
    tick: int,
    title: str,
    clip_floor: bool,
) -> None:
    def clip(y: np.ndarray) -> np.ndarray:
        if not clip_floor:
            return y
        return np.maximum(y, y_min)

    fig, ax = plt.subplots(figsize=(9, 4), facecolor="#0c0a14")
    ax.set_facecolor("#0c0a14")
    ax.semilogx(freqs, clip(o), "--", color="#9ca3af", linewidth=1.5, label="Original")
    ax.semilogx(freqs, clip(r), color="#f97316", linewidth=1.75, label="Reference")
    ax.semilogx(freqs, clip(m), color="#ec4899", linewidth=2.5, label="Remastered")
    ax.set_xlim(20, 20000)
    ax.set_ylim(y_min, y_max)
    ticks = list(np.arange(y_min, y_max + 0.001, tick))
    ax.set_yticks(ticks)
    ax.set_ylabel("Relative dB")
    ax.set_xlabel("Frequency (Hz)")
    ax.grid(True, alpha=0.15, color="white")
    ax.tick_params(colors="#a39bb8")
    for sp in ax.spines.values():
        sp.set_color("#ffffff33")
    ax.legend(facecolor="#161224", edgecolor="#ffffff22", labelcolor="#f2f0fa")
    ax.set_title(title, color="#f2f0fa")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    out = Path("/opt/cursor/artifacts")
    out.mkdir(parents=True, exist_ok=True)
    freqs, o, r, m = _synthetic_curves()
    y0_min, y0_max, tick0 = _old_axis(o, r, m)
    _plot(
        out / "spectrum-yaxis-before.png",
        freqs,
        o,
        r,
        m,
        y0_min,
        y0_max,
        tick0,
        "Spectrum Y-axis (before)",
        clip_floor=False,
    )
    allv = list(o) + list(r) + list(m)
    y_min, y_max = compute_y_axis_range(allv)
    tick = choose_y_tick_step(y_min, y_max)
    _plot(
        out / "spectrum-yaxis-after.png",
        freqs,
        o,
        r,
        m,
        y_min,
        y_max,
        tick,
        f"Spectrum Y-axis (after, floor {Y_FLOOR_DB} dB)",
        clip_floor=True,
    )
    print("wrote", out / "spectrum-yaxis-before.png", out / "spectrum-yaxis-after.png")


if __name__ == "__main__":
    main()