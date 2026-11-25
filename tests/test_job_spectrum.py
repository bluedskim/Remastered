from __future__ import annotations

import json
import sqlite3
import wave
from pathlib import Path

import numpy as np
import pytest
from scipy import signal

from remastered.web.db import SCHEMA_VERSION, JobStore
from tests.job_wait import wait_job_complete
from remastered.web.job_spectrum import (
    _path_looks_like_wav,
    NUM_POINTS,
    SPECTRUM_JSON_VERSION,
    _lufs_gain,
    compute_spectrum_comparison,
    curve_from_mono,
    parse_stored_spectrum,
    spectrum_json_bytes,
)


def _write_stereo_wav(path: Path, left: np.ndarray, right: np.ndarray, sr: int = 48000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))), 1e-6)
    scale = 0.2 / peak
    frames = np.stack([np.clip(left * scale, -1, 1), np.clip(right * scale, -1, 1)], axis=1)
    pcm = (frames * 32767.0).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _pink(n: int, sr: int, rng: np.random.Generator) -> np.ndarray:
    white = rng.standard_normal(n)
    return signal.filtfilt(*signal.butter(1, 2.0 / (sr / 2), btype="high"), white)


def test_worker_finalize_does_not_call_spectrum_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"x")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000, "title": "Worker"},
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "remastered.web.job_spectrum.measure_and_store_job_spectrum",
        lambda *a, **k: calls.append("spectrum"),
    )

    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")
    from remastered.web.app import fastapi_application

    with TestClient(fastapi_application) as client:
        client.get("/")
        resp = client.post(
            "/jobs",
            data={
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "reference_name": "ref.wav",
            },
            follow_redirects=False,
        )
        job_id = resp.headers["location"].split("/")[-1]
        wait_job_complete(client, job_id)
    assert calls == []


def test_migration_v17_to_v18_adds_spectrum_column(tmp_path: Path) -> None:
    db_path = tmp_path / "v17.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 17")
    conn.executescript(
        """
        CREATE TABLE users (id TEXT PRIMARY KEY, google_sub TEXT NOT NULL UNIQUE,
            email TEXT, name TEXT, picture TEXT, encrypted_token BLOB,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
            youtube_url TEXT NOT NULL, reference_path TEXT NOT NULL, title TEXT,
            description TEXT NOT NULL DEFAULT '', privacy TEXT NOT NULL DEFAULT 'private',
            upload_enabled INTEGER NOT NULL DEFAULT 0, error TEXT, source_video_id TEXT,
            uploaded_video_id TEXT, audio_path TEXT, mastered_path TEXT, video_path TEXT,
            stage_state TEXT NOT NULL DEFAULT '{}', files_cleaned INTEGER NOT NULL DEFAULT 0,
            client_ip TEXT, stereo_width_mode TEXT NOT NULL DEFAULT 'follow_reference',
            reference_source_type TEXT NOT NULL DEFAULT 'preset', reference_youtube_url TEXT,
            reference_video_id TEXT, reference_youtube_title TEXT,
            processing_stage TEXT NOT NULL DEFAULT 'queued',
            final_tp_protection INTEGER NOT NULL DEFAULT 1, memo TEXT NOT NULL DEFAULT '',
            keep_source_treble_above_reference_cutoff INTEGER NOT NULL DEFAULT 1,
            lufs_original REAL, lufs_mastered_wav REAL
        );
        CREATE TABLE job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
            created_at TEXT NOT NULL, message TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    JobStore(db_path)
    with JobStore(db_path)._conn() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert "spectrum_comparison_json" in cols
    assert version == SCHEMA_VERSION


def test_pink_vs_lowpass_high_band_tilt() -> None:
    sr = 48_000
    n = sr * 3
    rng = np.random.default_rng(1)
    pink = _pink(n, sr, rng)
    dark = signal.filtfilt(*signal.butter(8, 600.0 / (sr / 2), btype="low"), pink)
    rel_pink = curve_from_mono(pink, sr)
    rel_dark = curve_from_mono(dark, sr)
    grid = np.geomspace(20, 20_000, NUM_POINTS)
    high = grid >= 2_000
    assert float(np.nanmean(rel_pink[high] - rel_dark[high])) > 4.0


def test_lufs_gain_scales_rms() -> None:
    sig = np.ones(1000) * 0.01
    louder = _lufs_gain(sig, -30.0)
    assert float(np.sqrt(np.mean(louder * louder))) > float(np.sqrt(np.mean(sig * sig)))


def test_skip_stub_wav_without_soundfile() -> None:
    import tempfile

    stub = Path(tempfile.mkdtemp()) / "stub.wav"
    stub.write_bytes(b"RIFF" + b"\x00" * 96)
    assert not _path_looks_like_wav(stub)
    bad = stub.parent / "tiny.wav"
    bad.write_bytes(b"x")
    assert not _path_looks_like_wav(bad)


def test_compute_and_json_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sr = 48_000
    n = sr * 2
    rng = np.random.default_rng(2)
    sig = _pink(n, sr, rng)
    paths = tmp_path / "o.wav", tmp_path / "r.wav", tmp_path / "m.wav"
    for p in paths:
        _write_stereo_wav(p, sig, sig, sr)

    monkeypatch.setattr(
        "remastered.web.job_spectrum.measure_integrated_lufs",
        lambda _p, **_: -20.0,
    )
    payload = compute_spectrum_comparison(
        original_wav=paths[0],
        reference_wav=paths[1],
        mastered_wav=paths[2],
    )
    assert payload is not None
    assert payload["v"] == SPECTRUM_JSON_VERSION
    assert len(payload["f"]) == NUM_POINTS
    assert len(payload["o"]) == len(payload["r"]) == len(payload["m"]) == NUM_POINTS
    assert spectrum_json_bytes(payload) < 12_000
    assert parse_stored_spectrum(json.dumps(payload)) == payload


def test_job_detail_shows_spectrum_panel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from fastapi.testclient import TestClient

    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"x")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    monkeypatch.setattr(
        "remastered.web.app.media_exists",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000, "title": "Spectrum Track"},
    )

    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")
    from remastered.config import resolve_settings
    from remastered.web.app import fastapi_application
    from remastered.web.db import JobStore

    fake = {
        "v": 1,
        "f": [100.0, 1000.0],
        "o": [0.0, 0.0],
        "r": [1.0, 1.0],
        "m": [2.0, 2.0],
    }

    with TestClient(fastapi_application) as client:
        client.get("/")
        resp = client.post(
            "/jobs",
            data={
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "reference_name": "ref.wav",
            },
            follow_redirects=False,
        )
        job_id = resp.headers["location"].split("/")[-1]
        wait_job_complete(client, job_id)
        store = JobStore(resolve_settings().work_dir / "remastered_jobs.db")
        store.update_job(job_id, spectrum_comparison_json=json.dumps(fake))
        detail = client.get(f"/jobs/{job_id}")
        assert detail.status_code == 200
        assert "spectrum-panel" in detail.text
        assert "Spectrum" in detail.text
        assert "volume-matched" in detail.text


def test_job_detail_hides_spectrum_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from fastapi.testclient import TestClient

    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"x")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    monkeypatch.setattr(
        "remastered.web.app.media_exists",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000, "title": "No Spectrum"},
    )

    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")
    from remastered.web.app import fastapi_application

    with TestClient(fastapi_application) as client:
        client.get("/")
        resp = client.post(
            "/jobs",
            data={
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "reference_name": "ref.wav",
            },
            follow_redirects=False,
        )
        job_id = resp.headers["location"].split("/")[-1]
        wait_job_complete(client, job_id)
        detail = client.get(f"/jobs/{job_id}")
        assert detail.status_code == 200
        assert "spectrum-panel" not in detail.text


def test_spectrum_api_access_control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from fastapi.testclient import TestClient

    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"x")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )
    monkeypatch.setattr(
        "remastered.web.app.media_exists",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000, "title": "API Spectrum"},
    )

    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")
    from remastered.config import resolve_settings
    from remastered.web.app import fastapi_application
    from remastered.web.db import JobStore

    fake = {"v": 1, "f": [100.0], "o": [0.0], "r": [0.0], "m": [0.0]}

    with TestClient(fastapi_application) as client:
        client.get("/")
        resp = client.post(
            "/jobs",
            data={
                "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "reference_name": "ref.wav",
            },
            follow_redirects=False,
        )
        job_id = resp.headers["location"].split("/")[-1]
        wait_job_complete(client, job_id)
        JobStore(resolve_settings().work_dir / "remastered_jobs.db").update_job(
            job_id,
            spectrum_comparison_json=json.dumps(fake),
        )
        ok = client.get(f"/api/jobs/{job_id}/spectrum-comparison")
        assert ok.status_code == 200
        assert ok.json()["v"] == 1

    with TestClient(fastapi_application) as stranger:
        denied = stranger.get(f"/api/jobs/{job_id}/spectrum-comparison")
        assert denied.status_code == 404