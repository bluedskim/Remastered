from __future__ import annotations

from pathlib import Path

from remastered.web.db import JobStore


def test_job_store_create_and_update(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    user_id = store.upsert_user(
        google_sub="sub123",
        email="a@example.com",
        name="A",
        picture=None,
        encrypted_token=b"enc",
    )
    job_id = store.create_job(
        user_id=user_id,
        youtube_url="https://www.youtube.com/watch?v=test",
        reference_path="/references/ref.wav",
        title="테스트",
        description="설명",
        privacy="private",
        upload_enabled=True,
    )
    store.append_log(job_id, "대기 중")
    store.update_job(job_id, status="running", stage="download")
    job = store.get_job(job_id, user_id=user_id)
    assert job is not None
    assert job["status"] == "running"
    assert store.get_job(job_id, user_id="other") is None
    assert store.count_user_active_jobs(user_id) == 1
