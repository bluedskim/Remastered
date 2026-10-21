from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import app as asgi_app, fastapi_application
from remastered.web.stream_audio import playback_path_for_wav


@pytest.fixture
def media_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"x")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_WEB_MAX_CONCURRENT_MEDIA_STREAMS", "4")

    def fake_transcode(wav: Path) -> Path:
        out = playback_path_for_wav(wav)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 8192)
        return out

    monkeypatch.setattr("remastered.web.worker.transcode_wav_for_playback", fake_transcode)

    def fake_web_pipeline(url: str, reference: Path, settings, progress=None):
        owner_work = settings.work_dir
        audio = owner_work / "vid" / "audio.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"RIFF" + b"\x00" * 4096)
        mastered = owner_work / "vid" / "master" / "out.wav"
        mastered.parent.mkdir(parents=True, exist_ok=True)
        mastered.write_bytes(b"RIFF" + b"\x00" * 4096)
        fake_transcode(audio)
        fake_transcode(mastered)
        return {"audio": audio, "mastered": mastered, "video_id": "vid"}

    monkeypatch.setattr("remastered.web.worker.run_web_master_pipeline", fake_web_pipeline)
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 60, "filesize": 1000},
    )

    with TestClient(fastapi_application) as client:
        yield client


def test_health_endpoint_independent() -> None:
    with TestClient(asgi_app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_responsive_during_concurrent_media(media_client: TestClient) -> None:
    media_client.get("/")
    post = media_client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=stress",
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    job_id = post.headers["location"].split("/")[-1]
    for _ in range(40):
        if media_client.get(f"/jobs/{job_id}/panel").status_code == 286:
            break
        time.sleep(0.05)
    else:
        pytest.fail("job did not complete")

    def fetch_range() -> int:
        return media_client.get(
            f"/media/{job_id}/mastered",
            headers={"Range": "bytes=0-1023"},
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(fetch_range) for _ in range(12)]
        for _ in range(5):
            start = time.monotonic()
            with TestClient(asgi_app) as health_client:
                health = health_client.get("/health")
            elapsed = time.monotonic() - start
            assert health.status_code == 200
            assert elapsed < 2.0
        codes = {f.result() for f in futures}
    assert 206 in codes or 200 in codes
