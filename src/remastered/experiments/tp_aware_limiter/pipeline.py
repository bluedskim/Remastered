"""Isolated matchering runs with selectable limiter (harness / dev only)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal

import matchering as mg

LimiterKind = Literal["stock", "tp_aware"]


@contextmanager
def _limiter_context(kind: LimiterKind) -> Iterator[None]:
    if kind == "stock":
        yield
        return
    import matchering.limiter.hyrax as hyrax
    import matchering.stages as stages
    from remastered.experiments.tp_aware_limiter.hyrax_tp import limit as limit_tp

    stock = hyrax.limit
    hyrax.limit = limit_tp
    stages.limit = limit_tp
    try:
        yield
    finally:
        hyrax.limit = stock
        stages.limit = stock


def run_follow_reference_master(
    target_wav: Path,
    reference_wav: Path,
    output_wav: Path,
    *,
    limiter: LimiterKind = "stock",
    bit_depth: int = 16,
    temp_dir: Path | None = None,
) -> Path:
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    config = mg.Config()
    if temp_dir:
        config.temp_folder = str(temp_dir)
    subtype = "PCM_16" if bit_depth == 16 else "PCM_24"
    with _limiter_context(limiter):
        mg.process(
            str(target_wav),
            str(reference_wav),
            [mg.Result(str(output_wav), subtype)],
            config=config,
        )
    return output_wav
