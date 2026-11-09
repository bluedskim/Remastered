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
    """Map rendered WAV gain (via side effects) to table peaks."""

    state: dict[str, float | None] = {"last_gain": None}

    def fake_apply_gain(src: Path, dest: Path, gain_db: float, **kwargs) -> None:
        state["last_gain"] = round(gain_db, 2)

    def _peak_for_gain(gain: float, *, aac: bool) -> float:
        key = round(gain, 2)
        if key not in table:
            raise KeyError(f"missing peak table entry for gain {gain}")
        wav, aac_p = table[key]
        return aac_p if aac else wav

    def fake_measure(path: Path, **kwargs) -> float:
        if path.suffix.lower() == ".m4a":
            g = state["last_gain"]
            assert g is not None
            return _peak_for_gain(g, aac=True)
        if path.name.startswith("mastered_matchering") or "in" in path.name:
            return raw_peak
        g = state["last_gain"]
        if g is None:
            return raw_peak
        return _peak_for_gain(g, aac=False)

    return fake_measure, fake_apply_gain, state


def test_job60a8_sweep_refines_to_minus_3_10() -> None:
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
        adopted, pw, pa, g_pass = apply_final_true_peak_protection(
            Path("mastered_matchering.wav"),
            Path("out.wav"),
            ws=ws,
        )
    assert adopted == pytest.approx(-3.1, abs=0.02)
    assert g_pass == pytest.approx(-5.75, abs=0.02)
    assert adopted > g_pass
    ceil = pass_ceiling_dbtp(ws)
    assert pw <= ceil and pa <= ceil


def test_monotonic_bc44_like_keeps_phase1_pass_when_shallower_fail() -> None:
    """Shallower refine candidates fail AAC; adopted gain stays at phase-1 pass."""
    peaks = [
        0.4,
        -1.0,
        -0.7,
        -1.4,
        -1.1,
        -1.3,
        -0.4,
        -1.4,
        -1.1,
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
        adopted, pw, pa, g_pass = apply_final_true_peak_protection(
            Path("in.wav"),
            Path("out.wav"),
            ws=ws,
        )
    assert g_pass < 0
    assert adopted == pytest.approx(g_pass)
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
        adopted, _, _, g_pass = apply_final_true_peak_protection(
            Path("in.wav"),
            Path("out.wav"),
            ws=WebSettings(
                true_peak_refine_max_attempts=0,
                true_peak_pass_headroom_db=0.05,
            ),
        )
    assert adopted == 0.0
    assert g_pass == 0.0


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
        adopted, pw, pa, g_pass = apply_final_true_peak_protection(
            Path("in.wav"),
            Path("out.wav"),
            ws=WebSettings(true_peak_refine_max_attempts=10),
        )
    assert adopted == g_pass == 0.0
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


def test_verify_delivered_playback_rerenders_at_g_pass(tmp_path: Path) -> None:
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
        verify_delivered_playback_peak(
            mastered,
            m4a,
            phase1_pass_gain_db=-4.0,
            ws=WebSettings(),
        )
    tr.assert_called_once()
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
        return (-3.0, -2.0, -1.2, -5.0)

    monkeypatch.setattr(
        "remastered.web.worker.apply_final_true_peak_protection",
        fake_tp,
    )
    monkeypatch.setattr(
        "remastered.web.worker.copy_matchering_to_raw",
        lambda m: m.with_name("mastered_matchering_16bit.wav"),
    )

    def fake_transcode(p):
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
