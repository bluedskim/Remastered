#!/usr/bin/env python3
"""Compare stock vs TP-aware Hyrax limiter on job-60 fixtures (isolated)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

FLAC_DIR = Path(os.environ.get("JOB60_FLAC_DIR", "/opt/cursor/artifacts/job-60f09377-limiter-compare/flac"))
OUT_DIR = Path(os.environ.get("TP_HYRAX_OUT", "/opt/cursor/artifacts/tp-hyrax-experiment"))
EXCERPT_S = float(os.environ.get("JOB60_EXCERPT_SECONDS", "0"))
PREVIEW_START_S = float(os.environ.get("JOB60_PREVIEW_START_S", "30"))
PREVIEW_DUR_S = float(os.environ.get("JOB60_PREVIEW_DUR_S", "18"))
TARGET_LUFS = float(os.environ.get("PREVIEW_TARGET_LUFS", "-14"))


def _flac_to_wav(flac: Path, wav: Path) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(flac)]
    if EXCERPT_S > 0:
        cmd.extend(["-t", str(EXCERPT_S)])
    cmd.extend(["-c:a", "pcm_s16le", str(wav)])
    subprocess.run(cmd, check=True)


def _common_constant_gain_db(lufs_values: list[float], target: float) -> float:
    """Attenuation-only gain so every file can sit at or below target LUFS."""
    gain_db = min(target - l for l in lufs_values)
    return min(0.0, gain_db)


def main() -> int:
    from remastered.experiments.tp_aware_limiter.metrics import (
        apply_constant_gain_db,
        apply_delivery_tp_guard,
        measure_with_aac,
    )
    from remastered.experiments.tp_aware_limiter.pipeline import run_follow_reference_master
    from remastered.web.settings import WebSettings

    work = OUT_DIR / "work"
    work.mkdir(parents=True, exist_ok=True)
    deliver = OUT_DIR / "delivery"
    deliver.mkdir(parents=True, exist_ok=True)

    src_flac = FLAC_DIR / "source_56hqrlQxMMI.flac"
    ref_flac = FLAC_DIR / "reference_P4AFXn7b9Ys.flac"
    if not src_flac.is_file() or not ref_flac.is_file():
        print("Missing job-60 FLAC inputs; set JOB60_FLAC_DIR", file=sys.stderr)
        return 2

    target = work / "source.wav"
    reference = work / "reference.wav"
    _flac_to_wav(src_flac, target)
    _flac_to_wav(ref_flac, reference)

    ws = WebSettings()
    variants: dict[str, dict] = {}
    timings: dict[str, float] = {}
    t0 = time.monotonic()

    for limiter in ("stock", "tp_aware"):
        raw = work / f"mastered_{limiter}_raw.wav"
        t_lim = time.monotonic()
        print(f"=== matchering ({limiter}) ===", flush=True)
        run_follow_reference_master(
            target, reference, raw, limiter=limiter, temp_dir=work / f"tmp_{limiter}"
        )
        timings[f"matchering_{limiter}_s"] = round(time.monotonic() - t_lim, 2)

        guarded = work / f"job60_{limiter}_with_tp_guard.wav"
        aac_out = work / f"job60_{limiter}_with_tp_guard.m4a"
        tp_meta = apply_delivery_tp_guard(raw, guarded, ws=ws)
        raw_m = measure_with_aac(raw, aac_path=work / f"{limiter}_raw.m4a")
        guard_m = measure_with_aac(guarded, aac_path=aac_out)
        variants[limiter] = {
            "raw": raw_m,
            "with_tp_guard": {**guard_m, **tp_meta},
        }

    # Constant-gain loudness-matched excerpts (guard outputs, same segment, no loudnorm)
    seg_paths: list[Path] = []
    seg_lufs: list[float] = []
    for limiter in ("stock", "tp_aware"):
        guarded = work / f"job60_{limiter}_with_tp_guard.wav"
        seg = deliver / f"preview_{limiter}_guarded_seg_{int(PREVIEW_DUR_S)}s.wav"
        apply_constant_gain_db(
            guarded,
            seg,
            0.0,
            segment_start_s=PREVIEW_START_S,
            segment_duration_s=PREVIEW_DUR_S,
        )
        from remastered.experiments.tp_aware_limiter.ffmpeg_ebur128 import measure_loudness

        loud = measure_loudness(seg)
        if loud.get("lufs_i") is None:
            print(f"LUFS parse failed for {seg}", file=sys.stderr)
            return 3
        seg_paths.append(seg)
        seg_lufs.append(float(loud["lufs_i"]))

    gain_db = _common_constant_gain_db(seg_lufs, TARGET_LUFS)
    preview_meta = []
    for limiter, seg in zip(("stock", "tp_aware"), seg_paths, strict=True):
        matched = deliver / f"preview_{limiter}_guarded_constgain_I{TARGET_LUFS:.0f}dB.wav"
        apply_constant_gain_db(
            work / f"job60_{limiter}_with_tp_guard.wav",
            matched,
            gain_db,
            segment_start_s=PREVIEW_START_S,
            segment_duration_s=PREVIEW_DUR_S,
        )
        preview_meta.append({"variant": limiter, "file": matched.name, "gain_db": gain_db})

    zip_path = OUT_DIR / "job60_ab_previews_pcm16.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in seg_paths:
            zf.write(p, p.name)
        for limiter in ("stock", "tp_aware"):
            matched = deliver / f"preview_{limiter}_guarded_constgain_I{TARGET_LUFS:.0f}dB.wav"
            zf.write(matched, matched.name)

    report = {
        "job_id": "60f09377",
        "elapsed_total_s": round(time.monotonic() - t0, 1),
        "excerpt_s": EXCERPT_S or "full",
        "timings_s": timings,
        "production_tp_settings": {
            "ceiling_dbtp": ws.true_peak_ceiling_dbtp,
            "aac_headroom_db": ws.true_peak_pass_headroom_db,
            "max_attempts": ws.true_peak_max_attempts,
        },
        "variants": variants,
        "previews": {
            "segment_start_s": PREVIEW_START_S,
            "segment_duration_s": PREVIEW_DUR_S,
            "target_lufs": TARGET_LUFS,
            "common_gain_db": gain_db,
            "constant_gain_only": True,
            "files": preview_meta,
            "zip": str(zip_path),
        },
        "interpretation_notes": [
            "Limiter gain reduction alters dynamics; constant post-gain cannot undo that shaping.",
            "Inter-sample peaks above sample peaks may be reduced by attenuation alone (no limiter).",
            "strict -1.0 dBTP on AAC may fail while production pass rule (ceiling+headroom) passes.",
        ],
    }

    parse_failures: list[str] = []
    guard_failures: list[str] = []
    strict_misses: list[str] = []
    for limiter, v in variants.items():
        for label in ("raw", "with_tp_guard"):
            block = v[label]
            for key in ("lufs_i", "dbtp"):
                if block.get(key) is None or block.get("parse_error"):
                    parse_failures.append(f"{limiter}.{label}.{key}")
        g = v["with_tp_guard"]
        if g.get("guard_verdict") == "fail":
            guard_failures.append(limiter)
        if not g.get("pass_strict_aac"):
            strict_misses.append(f"{limiter}.aac_dbtp={g.get('aac_peak_dbtp')}")
        if not g.get("pass_strict_wav"):
            strict_misses.append(f"{limiter}.wav_dbtp={g.get('wav_peak_dbtp')}")
    report["parse_failures"] = parse_failures
    report["guard_failures"] = guard_failures
    report["strict_-1dBTP_misses"] = strict_misses
    report["validation_ok"] = not parse_failures and not guard_failures

    report_path = OUT_DIR / "metrics_job60.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {report_path}", flush=True)
    print(
        f"validation_ok={report['validation_ok']} parse={parse_failures} guard={guard_failures}",
        flush=True,
    )
    return 0 if report["validation_ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
