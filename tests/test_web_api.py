from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"test-audio")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.delenv("REMASTERED_WEB_PASSWORD", raising=False)

    def fake_pipeline(
        url: str,
        reference: Path,
        settings,
        progress=None,
        **kwargs,
    ):
        prog = progress
        if prog:
            prog.stage_start("download")
            prog.log("mock download")
            prog.stage_done("download")
            prog.stage_start("master")
            prog.stage_done("master")
            prog.stage_start("render")
            prog.stage_done("render")
            if not kwargs.get("skip_upload"):
                prog.stage_start("upload")
                prog.stage_done("upload")
        audio = work / "vid" / "audio.wav"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"wav")
        mastered = work / "vid" / "master" / "out.wav"
        mastered.parent.mkdir(parents=True, exist_ok=True)
        mastered.write_bytes(b"wav")
        video = work / "vid" / "vid_remastered.mp4"
        video.write_bytes(b"mp4")
        result = {
            "audio": audio,
            "mastered": mastered,
            "video": video,
            "video_id": "vid",
        }
        if not kwargs.get("skip_upload"):
            result["uploaded_video_id"] = "UPLOADED1"
        return result

    monkeypatch.setattr("remastered.web.worker.run_pipeline_progress", fake_pipeline)

    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_index_and_create_job(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert "새 작업" in home.text

    resp = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "reference_name": "ref.wav",
            "title": "테스트 Remaster",
            "description": "웹 UI",
            "privacy": "private",
            "upload_enabled": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_url = resp.headers["location"]
    assert job_url.startswith("/jobs/")

    for _ in range(30):
        detail = client.get(job_url)
        assert detail.status_code == 200
        if "completed" in detail.text or "작업이 완료" in detail.text:
            break
        time.sleep(0.1)
    else:
        pytest.fail("job did not complete in time")

    panel = client.get(f"{job_url}/panel")
    assert panel.status_code == 200
    assert "status-completed" in panel.text or "UPLOADED1" in panel.text


def test_password_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(tmp_path / "work"))
    (tmp_path / "work").mkdir()
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(tmp_path / "refs"))
    (tmp_path / "refs").mkdir()
    monkeypatch.setenv("REMASTERED_WEB_PASSWORD", "secret-pass")

    with TestClient(app) as test_client:
        blocked = test_client.get("/", follow_redirects=False)
        assert blocked.status_code == 303
        assert blocked.headers["location"] == "/login"
        login = test_client.post(
            "/login",
            data={"password": "secret-pass"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        ok = test_client.get("/")
        assert ok.status_code == 200
