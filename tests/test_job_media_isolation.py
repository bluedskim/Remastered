from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.config import resolve_settings
from remastered.web.app import fastapi_application
from remastered.web.cleanup import delete_job_media_files
from remastered.web.db import JobStore
from remastered.web.job_media_paths import job_workspace_dir
from remastered.web.media import FILES_MISSING_MESSAGE, media_unavailable_message


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"test-audio")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_CREATE_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_WEB_DUPLICATE_JOB_WINDOW_SECONDS", "0")
    monkeypatch.setenv("REMASTERED_MAIL_MODE", "log")
    monkeypatch.setenv(
        "REMASTERED_JWT_SECRET",
        "test-jwt-secret-key-at-least-32-bytes!!",
    )

    from tests.worker_patches import install_fake_worker_pipeline

    install_fake_worker_pipeline(monkeypatch, reference_path=refs / "ref.wav")
    monkeypatch.setattr(
        "remastered.web.limits.inspect_youtube_source",
        lambda url: {"duration": 120, "filesize": 1000, "title": "Test Track"},
    )

    with TestClient(fastapi_application) as test_client:
        yield test_client


def _wait_job_complete(client: TestClient, job_id: str) -> None:
    for _ in range(40):
        panel = client.get(f"/jobs/{job_id}/panel")
        if panel.status_code == 286:
            return
        time.sleep(0.05)
    pytest.fail("job did not complete")


def _create_job(client: TestClient, youtube_url: str) -> str:
    client.get("/")
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": youtube_url,
            "reference_name": "ref.wav",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].split("/")[-1]
    _wait_job_complete(client, job_id)
    return job_id


def test_delete_one_job_does_not_remove_sibling_same_video(
    client: TestClient, tmp_path: Path
) -> None:
    url = "https://www.youtube.com/watch?v=sharedvid01"
    job_a = _create_job(client, url)
    job_b = _create_job(client, url)

    work_dir = resolve_settings().work_dir
    store = JobStore(work_dir / "remastered_jobs.db")
    row_a = store.get_job(job_a)
    row_b = store.get_job(job_b)
    assert row_a and row_b
    path_a = Path(row_a["mastered_path"])
    path_b = Path(row_b["mastered_path"])
    assert path_a.is_file() and path_b.is_file()
    assert path_a != path_b
    assert job_workspace_dir(work_dir, row_a["user_id"], job_a) != job_workspace_dir(
        work_dir, row_b["user_id"], job_b
    )

    assert client.delete(f"/api/jobs/{job_a}").status_code == 200
    assert not path_a.is_file()
    assert path_b.is_file()
    assert client.get(f"/media/{job_b}/mastered").status_code == 200


def test_legacy_delete_does_not_rmtree_shared_video_directory(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    owner = "user-legacy"
    video_id = "sharedvid"
    master_dir = work / "users" / owner / video_id / "master"
    master_dir.mkdir(parents=True)
    shared_master = master_dir / "mastered_16bit.wav"
    shared_master.write_bytes(b"SHARED")
    sentinel = master_dir / "keep.txt"
    sentinel.write_text("stay")

    job_a = {
        "id": "job-a",
        "user_id": owner,
        "source_video_id": video_id,
        "mastered_path": str(shared_master),
        "audio_path": "",
    }

    delete_job_media_files(job_a, work)
    assert not shared_master.is_file()
    assert sentinel.is_file()
    assert master_dir.is_dir()


def test_missing_files_message_not_retention_when_not_cleaned() -> None:
    job = {"files_cleaned": False, "status": "completed"}
    msg = media_unavailable_message(job, retention_days=14)
    assert "14-day" not in msg
    assert msg == FILES_MISSING_MESSAGE

    cleaned = media_unavailable_message({"files_cleaned": True}, retention_days=14)
    assert "14-day" in cleaned


def test_completed_job_detail_shows_missing_not_retention(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = _create_job(client, "https://www.youtube.com/watch?v=msgTest1")
    work_dir = resolve_settings().work_dir
    store = JobStore(work_dir / "remastered_jobs.db")
    row = store.get_job(job_id)
    assert row is not None
    if row.get("mastered_path"):
        Path(row["mastered_path"]).unlink(missing_ok=True)
    if row.get("audio_path"):
        Path(row["audio_path"]).unlink(missing_ok=True)

    detail = client.get(f"/jobs/{job_id}")
    assert detail.status_code == 200
    assert FILES_MISSING_MESSAGE.split(".")[0] in detail.text
    assert "14 days by default" not in detail.text
