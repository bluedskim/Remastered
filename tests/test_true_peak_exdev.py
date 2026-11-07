from __future__ import annotations

import errno
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from remastered.web.settings import WebSettings
from remastered.web.true_peak_protection import (
    _commit_staging_file,
    _measure_true_peak_dbtp,
    _write_attenuated_master,
    apply_final_true_peak_protection,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make_hot_wav(path: Path, *, duration: float = 0.4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=f=1100:d={duration}",
            "-af",
            "volume=18dB",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )


def test_commit_staging_file_exdev_copy_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staging = tmp_path / "stage.wav"
    dest = tmp_path / "dest.wav"
    staging.write_bytes(b"attenuated-content")
    dest.write_bytes(b"original-master")

    real_replace = os.replace

    def raise_exdev(src: os.PathLike[str], dst: os.PathLike[str]) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "replace", raise_exdev)
    _commit_staging_file(staging, dest)
    assert dest.read_bytes() == b"attenuated-content"
    assert not staging.exists()


def test_write_attenuated_uses_output_directory_not_system_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: staging must live beside the mastered WAV (not default /tmp)."""
    work = tmp_path / "work" / "users" / "u1" / "jobs" / "jid" / "vid" / "master"
    work.mkdir(parents=True)
    raw = work / "mastered_matchering_16bit.wav"
    out = work / "mastered_16bit.wav"
    _make_hot_wav(raw)
    original = raw.read_bytes()
    out.write_bytes(original)

    seen_dirs: list[str | None] = []
    real_ntf = tempfile.NamedTemporaryFile

    def tracking_ntf(*args, **kwargs):
        seen_dirs.append(kwargs.get("dir"))
        return real_ntf(*args, **kwargs)

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", tracking_ntf)

    _write_attenuated_master(raw, out, -3.0, ffmpeg_path="ffmpeg")

    assert seen_dirs, "expected staging tempfile"
    assert all(d == str(work) for d in seen_dirs)
    assert out.read_bytes() != original
    assert out.stat().st_size > 1000


def test_write_attenuated_failure_keeps_original_intact(tmp_path: Path) -> None:
    work = tmp_path / "work" / "master"
    work.mkdir(parents=True)
    raw = work / "mastered_matchering_16bit.wav"
    out = work / "mastered_16bit.wav"
    _make_hot_wav(raw)
    sentinel = b"SENTINEL-UNPROTECTED"
    out.write_bytes(sentinel)

    with patch(
        "remastered.web.true_peak_protection._apply_gain_wav",
        side_effect=RuntimeError("ffmpeg failed"),
    ):
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            _write_attenuated_master(raw, out, -2.0)

    assert out.read_bytes() == sentinel
    leftovers = list(work.glob(".tp_stage_*"))
    assert leftovers == []


def test_apply_tp_real_ffmpeg_measures_wav_and_aac(tmp_path: Path) -> None:
    work = tmp_path / "work" / "master"
    work.mkdir(parents=True)
    raw = work / "mastered_matchering_16bit.wav"
    out = work / "mastered_16bit.wav"
    _make_hot_wav(raw)
    shutil.copy2(raw, out)

    ws = WebSettings(
        true_peak_ceiling_dbtp=-1.0,
        true_peak_pass_headroom_db=0.05,
        true_peak_max_attempts=12,
        true_peak_min_step_db=0.05,
    )
    gain, peak_wav, peak_aac = apply_final_true_peak_protection(
        raw,
        out,
        ffmpeg_path="ffmpeg",
        ws=ws,
    )

    assert gain <= 0.0
    verify_wav = _measure_true_peak_dbtp(out, ffmpeg_path="ffmpeg")
    assert verify_wav is not None
    pass_ceil = ws.true_peak_ceiling_dbtp - ws.true_peak_pass_headroom_db
    assert peak_wav <= pass_ceil + 0.06
    assert peak_aac <= pass_ceil + 0.06
    assert verify_wav <= pass_ceil + 0.06
