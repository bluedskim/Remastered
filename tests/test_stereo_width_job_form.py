from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.stereo_width import DEFAULT_STEREO_WIDTH_MODE
from remastered.web.app import fastapi_application
from remastered.web.job_retry import build_job_retry_form_payload
from remastered.web.settings import WebSettings


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


def _wait_job_queued(client: TestClient, job_id: str) -> None:
    for _ in range(20):
        job = client.app.state.store.get_job(job_id)
        if job is not None:
            return
        time.sleep(0.02)
    pytest.fail("job not created")


def _post_job(client: TestClient, stereo_width_mode: str | None) -> str:
    data: dict[str, str] = {
        "youtube_url": "https://www.youtube.com/watch?v=stereoForm1",
        "reference_name": "ref.wav",
    }
    if stereo_width_mode is not None:
        data["stereo_width_mode"] = stereo_width_mode
    resp = client.post("/jobs", data=data, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    job_id = resp.headers["location"].split("/")[-1]
    _wait_job_queued(client, job_id)
    return job_id


@pytest.mark.parametrize(
    ("posted", "expected"),
    [
        (None, DEFAULT_STEREO_WIDTH_MODE),
        ("follow_reference", "follow_reference"),
        ("preserve", "preserve"),
        ("bogus", DEFAULT_STEREO_WIDTH_MODE),
        ("", DEFAULT_STEREO_WIDTH_MODE),
    ],
)
def test_create_job_stereo_width_modes(
    client: TestClient, posted: str | None, expected: str
) -> None:
    job_id = _post_job(client, posted)
    job = client.app.state.store.get_job(job_id)
    assert job is not None
    assert job.get("stereo_width_mode") == expected


def test_retry_payload_unknown_stereo_falls_back_to_default(tmp_path: Path) -> None:
    ws = WebSettings()
    refs = tmp_path / "refs"
    refs.mkdir()
    job = {
        "id": "j1",
        "youtube_url": "https://www.youtube.com/watch?v=x",
        "reference_path": str(refs / "ref.wav"),
        "reference_source_type": "preset",
        "stereo_width_mode": "legacy_invalid",
    }
    payload = build_job_retry_form_payload(
        job,
        shared_refs=["ref.wav"],
        user_refs=[],
        user_refs_dir=tmp_path / "user_refs",
        work_dir=tmp_path / "work",
        ws=ws,
    )
    assert payload["stereoWidthMode"] == DEFAULT_STEREO_WIDTH_MODE


def test_create_job_preserve_does_not_alter_reference_prefs_fields(client: TestClient) -> None:
    """Stereo choice is independent of reference pref persistence."""
    home = client.get("/")
    assert 'name="stereo_width_mode" value="preserve"' in home.text
    job_id = _post_job(client, "preserve")
    job = client.app.state.store.get_job(job_id)
    assert job is not None
    assert job.get("stereo_width_mode") == "preserve"
    assert job.get("final_tp_protection") is True
