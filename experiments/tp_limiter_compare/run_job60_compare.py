#!/usr/bin/env python3
"""Compare stock vs TP-aware Hyrax limiter on job-60 fixtures (isolated)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

FLAC_DIR = Path(os.environ.get("JOB60_FLAC_DIR", "/opt/cursor/artifacts/job-60f09377-limiter-compare/flac"))
OUT_DIR = Path(os.environ.get("TP_HYRAX_OUT", "/opt/cursor/artifacts/tp-hyrax-experiment"))
EXCERPT_S = float(os.environ.get("JOB60_EXCERPT_SECONDS", "0"))  # 0 = full track


def _flac_to_wav(flac: Path, wav: Path) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(flac)]
    if EXCERPT_S > 0:
        cmd.extend(["-t", str(EXCERPT_S)])
    cmd.extend(["-c:a", "pcm_s16le", str(wav)])
    subprocess.run(cmd, check=True)


def _loudnorm_preview(src: Path, dest: Path, *, label: str) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-af",
            "loudnorm=I=-14:TP=-1.5:LRA=11",
            str(dest),
        ],
        check=True,
    )


def main() -> int:
    from remastered.experiments.tp_aware_limiter.metrics import (
        apply_delivery_tp_guard,
        measure_with_aac,
    )
    from remastered.experiments.tp_aware_limiter.pipeline import run_follow_reference_master

    work = OUT_DIR / "work"
    work.mkdir(parents=True, exist_ok=True)
    src_flac = FLAC_DIR / "source_56hqrlQxMMI.flac"
    ref_flac = FLAC_DIR / "reference_P4AFXn7b9Ys.flac"
    if not src_flac.is_file() or not ref_flac.is_file():
        print("Missing job-60 FLAC inputs; set JOB60_FLAC_DIR", file=sys.stderr)
        return 2

    target = work / "source.wav"
    reference = work / "reference.wav"
    _flac_to_wav(src_flac, target)
    _flac_to_wav(ref_flac, reference)

    variants: dict[str, dict] = {}
    t0 = time.monotonic()
    for limiter in ("stock", "tp_aware"):
        raw = work / f"mastered_{limiter}_raw.wav"
        print(f"=== matchering ({limiter}) ===", flush=True)
        run_follow_reference_master(
            target, reference, raw, limiter=limiter, temp_dir=work / f"tmp_{limiter}"
        )
        guarded = work / f"mastered_{limiter}_tp_guard.wav"
        shutil.copy2(raw, guarded)
        tp_meta = apply_delivery_tp_guard(raw, guarded)
        variants[limiter] = {
            "raw": measure_with_aac(raw),
            "tp_guard": {**measure_with_aac(guarded), **tp_meta},
        }

    report = {
        "job_id": "60f09377",
        "elapsed_s": round(time.monotonic() - t0, 1),
        "excerpt_s": EXCERPT_S or "full",
        "variants": variants,
        "diagnostic_baseline": {
            "stock_pre_limit_sample_peak_dbfs": 10.67,
            "stock_post_limit_sample_peak_dbfs": -0.016,
            "stock_post_limit_dbtp": 1.3,
        },
    }
    report_path = OUT_DIR / "metrics_job60.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {report_path}", flush=True)

    for name in ("stock", "tp_aware"):
        raw = work / f"mastered_{name}_raw.wav"
        prev = OUT_DIR / f"preview_{name}_raw_loudnorm_I-14.wav"
        _loudnorm_preview(raw, prev, label=name)
    print(f"Previews in {OUT_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
