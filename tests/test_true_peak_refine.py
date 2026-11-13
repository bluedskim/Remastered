"""Phase-2 true-peak refinement (non-monotonic AAC) and worker playback verify."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from remastered.web.settings import WebSettings
from remastered.web.true_peak_protection import (
    apply_final_true_peak_protection,
    pass_ceiling_dbtp,
    verify_delivered_playback_peak,
)

_FIXTURE_60A8 = Path(__file__).resolve().parent / "fixtures" / "tp_guard_60a8_table.json"


def _load_60a8_sweep() -> dict[float, tuple[float, float]]:
    data = json.loads(_FIXTURE_60A8.read_text(encoding="utf-8"))
    table: dict[float, tuple[float, float]] = {}
    for row in data["sweep"]["60a8"]:
        gain, wav, aac = row[0], row[1], row[2]
        table[round(gain, 2)] = (wav, aac)
    for row in data["algoC_log"]:
        _, gain, wav, aac = row
        table[round(gain, 2)] = (wav, aac)
    return table


def _make_sweep_measurer(
    table: dict[float, tuple[float, float]],
    *,
    raw_peak: float,
) -> callable:
    """Map WAV gain + preview extra (from raw) to sweep table peaks."""

    state: dict[str, float] = {"wav_gain": 0.0, "preview_extra": 0.0}

    def fake_apply_gain(src: Path, dest: Path, gain_db: float, **kwargs) -> None:
        rounded = round(gain_db, 2)
        if src.name.startswith("mastered_matchering") or "matchering" in src.name:
            state["wav_gain"] = rounded
        else:
            state["preview_extra"] = rounded

    def _peak_for_gain(gain: float, *, aac: bool) -> float:
        key = round(gain, 2)
        if key not in table:
            nearest = min(table.keys(), key=lambda k: abs(k - key))
            key = nearest
        wav, aac_p = table[key]
        return aac_p if aac else wav

    def _total_gain() -> float:
        return round(state["wav_gain"] + state["preview_extra"], 2)

    def fake_measure(path: Path, **kwargs) -> float:
        if path.suffix.lower() == ".m4a":
            return _peak_for_gain(_total_gain(), aac=True)
        if path.name.startswith("mastered_matchering") or "in" in path.name:
            return raw_peak
        return _peak_for_gain(state["wav_gain"], aac=False)

    return fake_measure, fake_apply_gain, state


def test_job60a8_sweep_wav_gain_shallower_than_legacy_shared_guard() -> None:
    table = _load_60a8_sweep()
    raw_peak = 0.6
    fake_measure, fake_apply_gain, _ = _make_sweep_measurer(table, raw_peak=raw_peak)
    ws = WebSettings(
        true_peak_ceiling_dbtp=-1.0,
        true_peak_pass_headroom_db=0.05,
        true_peak_max_attempts=12,
        true_peak_min_step_db=0.05,
        true_peak_refine_step_db=0.25,
        true_peak_refine_max_attempts=10,
    )

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch(
            "remastered.web.true_peak_protection._apply_gain_wav",
            side_effect=fake_apply_gain,
        ),
        patch("shutil.copy2"),
    ):
        wav_gain, pw, pa, preview_extra = apply_final_true_peak_protection(
            Path("mastered_matchering.wav"),
            Path("out.wav"),
            ws=ws,
        )
    ceil = pass_ceiling_dbtp(ws)
    assert pw <= ceil and pa <= ceil
    assert wav_gain > -3.1
    assert wav_gain < 0
    assert preview_extra <= 0.0
    assert abs(preview_extra) >= 0.0


def test_monotonic_bc44_like_preview_absorbs_aac_overshoot() -> None:
    """WAV refine may land below phase-1 pass; preview extra handles AAC-only overshoot."""
    peaks = [
        0.4,
        -1.0,
        -0.7,
        -1.4,
        -1.1,
        -1.3,
        -1.4,
        -1.1,
        -1.2,
        -1.05,
    ]

    def fake_measure(path: Path, **kwargs):
        return peaks.pop(0)

    ws = WebSettings(
        true_peak_ceiling_dbtp=-1.0,
        true_peak_pass_headroom_db=0.05,
        true_peak_min_step_db=0.05,
        true_peak_refine_step_db=0.25,
        true_peak_refine_max_attempts=10,
    )

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("remastered.web.true_peak_protection._apply_gain_wav"),
        patch("shutil.copy2"),
    ):
        wav_gain, pw, pa, preview_extra = apply_final_true_peak_protection(
            Path("in.wav"),
            Path("out.wav"),
            ws=ws,
        )
    assert wav_gain < 0
    assert preview_extra <= 0
    assert pw <= pass_ceiling_dbtp(ws)
    assert pa <= pass_ceiling_dbtp(ws)


def test_refine_max_attempts_zero_matches_legacy_single_pass() -> None:
    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            return_value=-2.0,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("shutil.copy2"),
    ):
        wav_gain, _, _, preview_extra = apply_final_true_peak_protection(
            Path("in.wav"),
            Path("out.wav"),
            ws=WebSettings(
                true_peak_refine_max_attempts=0,
                true_peak_pass_headroom_db=0.05,
            ),
        )
    assert wav_gain == 0.0
    assert preview_extra == 0.0


def test_no_refine_candidates_when_first_attempt_passes() -> None:
    peaks = [-2.0, -2.0]

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            return_value=-2.0,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("shutil.copy2"),
    ):
        wav_gain, pw, pa, preview_extra = apply_final_true_peak_protection(
            Path("in.wav"),
            Path("out.wav"),
            ws=WebSettings(true_peak_refine_max_attempts=10),
        )
    assert wav_gain == preview_extra == 0.0
    assert pw <= pass_ceiling_dbtp(WebSettings())


def test_phase1_twelve_failures_restore_and_raise(tmp_path: Path) -> None:
    wav_in = tmp_path / "in.wav"
    wav_out = tmp_path / "out.wav"
    wav_in.write_bytes(b"RAW")
    wav_out.write_bytes(b"RAW")

    def fake_measure(path: Path, **kwargs):
        if path.suffix.lower() == ".m4a":
            return 5.0
        return 2.0

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("remastered.web.true_peak_protection._apply_gain_wav"),
    ):
        with pytest.raises(RuntimeError, match="failed after 12 attempts"):
            apply_final_true_peak_protection(
                wav_in,
                wav_out,
                ws=WebSettings(
                    true_peak_max_attempts=12,
                    true_peak_pass_headroom_db=0.0,
                ),
            )
    assert wav_out.read_bytes() == b"RAW"


def test_verify_delivered_playback_retranscodes_with_extra_gain(tmp_path: Path) -> None:
    mastered = tmp_path / "mastered_16bit.wav"
    raw = tmp_path / "mastered_matchering_16bit.wav"
    m4a = tmp_path / "mastered_16bit.playback.m4a"
    mastered.write_bytes(b"m")
    raw.write_bytes(b"r")
    m4a.write_bytes(b"a")

    calls: list[str] = []

    measure_calls = {"n": 0}

    def fake_measure(path: Path, **kwargs):
        calls.append(path.suffix)
        if path == m4a:
            measure_calls["n"] += 1
            return 0.5 if measure_calls["n"] == 1 else -2.0
        return -2.0

    with (
        patch(
            "remastered.web.true_peak_protection._measure_true_peak_dbtp",
            side_effect=fake_measure,
        ),
        patch("remastered.web.true_peak_protection._encode_playback_aac"),
        patch("remastered.web.true_peak_protection._apply_gain_wav"),
        patch(
            "remastered.web.stream_audio.transcode_wav_for_playback",
            return_value=m4a,
        ) as tr,
    ):
        extra_out = verify_delivered_playback_peak(
            mastered,
            m4a,
            preview_extra_gain_db=-0.5,
            ws=WebSettings(),
        )
    tr.assert_called()
    assert extra_out < -0.5
    assert calls.count(".m4a") >= 2


def test_worker_playback_over_ceiling_fails_job(tmp_path: Path, monkeypatch) -> None:
    from remastered.download import DownloadResult
    from remastered.web.db import JobStore
    from remastered.web.worker import JobWorker
    from remastered.config import resolve_settings
    import shutil
    import time

    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    ref = refs / "ref.wav"
    ref.write_bytes(b"RIFF")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))

    def fake_download(url, settings, progress=None, on_downloaded=None):
        audio = settings.work_dir / "vid" / "audio.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"RIFF")
        return DownloadResult(
            video_id="vid",
            title="T",
            audio_path=audio,
            thumbnail_path=None,
            source_url=url,
        )

    def fake_master(dl, reference, settings, progress=None, stereo_width=None):
        mastered = settings.work_dir / "vid" / "master" / "mastered_16bit.wav"
        mastered.parent.mkdir(parents=True, exist_ok=True)
        mastered.write_bytes(b"RIFF")
        return {"audio": dl.audio_path, "mastered": mastered, "video_id": "vid", "title": "T"}

    monkeypatch.setattr("remastered.web.worker.download_source_audio", fake_download)
    monkeypatch.setattr(
        "remastered.web.worker.resolve_reference_wav",
        lambda job, settings, log=None: (ref, None),
    )
    monkeypatch.setattr("remastered.web.worker.master_downloaded_audio", fake_master)
    monkeypatch.setattr("remastered.web.worker.validate_youtube_source_limits", lambda url: None)

    def fake_tp(*_a, **_k):
        return (-1.95, -2.0, -1.2, -0.5)

    monkeypatch.setattr(
        "remastered.web.worker.apply_final_true_peak_protection",
        fake_tp,
    )
    monkeypatch.setattr(
        "remastered.web.worker.copy_matchering_to_raw",
        lambda m: m.with_name("mastered_matchering_16bit.wav"),
    )

    def fake_transcode(p, **kwargs):
        out = p.with_name(p.stem + ".playback.m4a")
        out.write_bytes(b"m4a")
        return out

    monkeypatch.setattr("remastered.web.worker.transcode_wav_for_playback", fake_transcode)

    def verify_boom(*_a, **_k):
        raise RuntimeError("True-peak protection failed after playback transcode verification.")

    monkeypatch.setattr("remastered.web.worker.verify_delivered_playback_peak", verify_boom)

    store = JobStore(work / "jobs.db")
    job_id = store.create_job(
        user_id="u",
        youtube_url="https://www.youtube.com/watch?v=x",
        reference_path=str(ref),
        title=None,
        description="",
        privacy="private",
        upload_enabled=False,
    )
    worker = JobWorker(store, resolve_settings())
    worker.start()
    try:
        for _ in range(120):
            job = store.get_job(job_id)
            assert job is not None
            if job["status"] == "failed":
                break
            time.sleep(0.05)
        else:
            pytest.fail("expected failed job")
        from remastered.web.job_user_messages import REASON_TP_VERIFY

        msg = job.get("error") or ""
        assert job.get("failure_reason") == REASON_TP_VERIFY or "try again" in msg.lower()
    finally:
        worker.stop()
