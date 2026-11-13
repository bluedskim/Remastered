"""Regression against retained job-60 stock matchering output (optional fixture)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from remastered.web.settings import WebSettings
from remastered.web.true_peak_protection import (
    _encode_playback_aac,
    _measure_true_peak_dbtp,
    apply_final_true_peak_protection,
    pass_ceiling_dbtp,
)

JOB60_RAW = Path(
    "/opt/cursor/artifacts/job-60f09377-limiter-compare/stages/repro_mastered_16bit.wav"
)
JOB60_FALLBACK = Path(
    "/opt/cursor/artifacts/tp-hyrax-experiment/work/mastered_stock_raw.wav"
)


def _fixture_path() -> Path | None:
    if JOB60_RAW.is_file():
        return JOB60_RAW
    if JOB60_FALLBACK.is_file():
        return JOB60_FALLBACK
    return None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_job60_stock_matchering_strict_tp(tmp_path: Path) -> None:
    src = _fixture_path()
    if src is None:
        pytest.skip("job-60 retained WAV not on this VM")

    raw = tmp_path / "mastered_matchering_16bit.wav"
    out = tmp_path / "mastered_16bit.wav"
    shutil.copy2(src, raw)
    shutil.copy2(src, out)

    ws = WebSettings(
        true_peak_ceiling_dbtp=-1.0,
        true_peak_pass_headroom_db=0.05,
        true_peak_max_attempts=12,
        true_peak_min_step_db=0.05,
    )
    before_wav = _measure_true_peak_dbtp(raw)
    before_m4a = tmp_path / "before.m4a"
    _encode_playback_aac(raw, before_m4a)
    before_aac = _measure_true_peak_dbtp(before_m4a)

    t0 = time.monotonic()
    gain, peak_wav, peak_aac, preview_extra = apply_final_true_peak_protection(
        raw, out, ffmpeg_path="ffmpeg", ws=ws
    )
    elapsed = time.monotonic() - t0

    pass_ceil = pass_ceiling_dbtp(ws)
    post_wav = _measure_true_peak_dbtp(out)
    post_m4a = tmp_path / "after.m4a"
    if preview_extra < -1e-9:
        from remastered.web.true_peak_protection import _apply_gain_wav

        guarded = tmp_path / "preview_src.wav"
        _apply_gain_wav(out, guarded, preview_extra, ffmpeg_path="ffmpeg")
        _encode_playback_aac(guarded, post_m4a, ffmpeg_path="ffmpeg")
    else:
        _encode_playback_aac(out, post_m4a, ffmpeg_path="ffmpeg")
    post_aac = _measure_true_peak_dbtp(post_m4a)

    proof = {
        "fixture": str(src),
        "before_wav_dbtp": before_wav,
        "before_aac_dbtp": before_aac,
        "gain_db": gain,
        "reported_wav": peak_wav,
        "reported_aac": peak_aac,
        "after_wav_dbtp": post_wav,
        "after_aac_dbtp": post_aac,
        "pass_ceiling_dbtp": pass_ceil,
        "attempts_wall_s": round(elapsed, 2),
    }
    out_dir = Path("/opt/cursor/artifacts/tp-strict-ceiling-proof")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "job60_before_after.json").write_text(
        json.dumps(proof, indent=2), encoding="utf-8"
    )

    assert post_wav is not None and post_aac is not None
    assert post_wav <= pass_ceil + 0.08
    assert post_aac <= pass_ceil + 0.08
    assert gain <= 0.0
    assert preview_extra <= 0.0
