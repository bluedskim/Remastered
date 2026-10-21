from __future__ import annotations

from pathlib import Path

from remastered.web.db import JobStore


def test_job_store_create_and_update(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    job_id = store.create_job(
        youtube_url="https://www.youtube.com/watch?v=test",
        reference_path="/references/ref.wav",
        title="테스트",
        description="설명",
        privacy="private",
        upload_enabled=True,
    )
    store.append_log(job_id, "대기 중")
    store.update_job(job_id, status="running", stage="download")
    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "running"
    assert job["title"] == "테스트"
    logs = store.get_logs(job_id)
    assert len(logs) == 1
    assert store.next_pending() is None

    job_id2 = store.create_job(
        youtube_url="https://youtu.be/abc",
        reference_path="/references/a.wav",
        title=None,
        description="",
        privacy="unlisted",
        upload_enabled=False,
    )
    assert store.next_pending() == job_id2
