from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.stereo_width import DEFAULT_STEREO_WIDTH_MODE
from remastered.web.app import fastapi_application
from remastered.web.db import JobStore
from remastered.web.job_retry import build_job_retry_form_payload
from remastered.web.reference_youtube import youtube_reference_path_key
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


def _wait_job_complete(client: TestClient, job_id: str) -> None:
    for _ in range(40):
        panel = client.get(f"/jobs/{job_id}/panel")
        if panel.status_code == 286:
            return
        time.sleep(0.05)
    pytest.fail("job did not complete")


def _create_completed_job(
    client: TestClient,
    *,
    youtube_url: str = "https://www.youtube.com/watch?v=retryTest1",
    stereo_width_mode: str = DEFAULT_STEREO_WIDTH_MODE,
) -> str:
    client.get("/")
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": youtube_url,
            "reference_name": "ref.wav",
            "stereo_width_mode": stereo_width_mode,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].split("/")[-1]
    _wait_job_complete(client, job_id)
    return job_id


def test_build_job_retry_form_payload_preset(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"x")
    ws = WebSettings()
    job = {
        "youtube_url": "https://www.youtube.com/watch?v=abc123",
        "reference_source_type": "preset",
        "reference_path": str(refs / "ref.wav"),
        "stereo_width_mode": "preserve",
    }
    payload = build_job_retry_form_payload(
        job,
        shared_refs=["ref.wav"],
        user_refs=[],
        user_refs_dir=tmp_path / "user_refs",
        ws=ws,
    )
    assert payload["sourceYoutubeUrl"] == job["youtube_url"]
    assert payload["tab"] == "preset"
    assert payload["presetName"] == "ref.wav"
    assert payload["presetLibrary"] == "shared"
    assert payload["stereoWidthMode"] == "preserve"
    assert payload["warnings"] == []


def test_build_job_retry_form_payload_missing_upload_warns(tmp_path: Path) -> None:
    ws = WebSettings()
    user_refs = tmp_path / "user_refs"
    user_refs.mkdir()
    job = {
        "youtube_url": "https://www.youtube.com/watch?v=abc123",
        "reference_source_type": "upload",
        "reference_path": str(user_refs / "gone.wav"),
        "stereo_width_mode": "follow_reference",
    }
    payload = build_job_retry_form_payload(
        job,
        shared_refs=[],
        user_refs=[],
        user_refs_dir=user_refs,
        ws=ws,
    )
    assert payload["tab"] == "preset"
    assert payload["uploadFilename"] == ""
    assert payload["warnings"]


def test_build_job_retry_form_payload_youtube_reference() -> None:
    ws = WebSettings()
    job = {
        "youtube_url": "https://www.youtube.com/watch?v=source1",
        "reference_source_type": "youtube",
        "reference_path": youtube_reference_path_key("refvid"),
        "reference_youtube_url": "https://www.youtube.com/watch?v=refvid",
        "stereo_width_mode": "follow_reference",
    }
    payload = build_job_retry_form_payload(
        job,
        shared_refs=["ref.wav"],
        user_refs=[],
        user_refs_dir=Path("/tmp/unused"),
        ws=ws,
    )
    assert payload["tab"] == "youtube"
    assert payload["youtubeUrl"] == job["reference_youtube_url"]
    assert payload["sourceYoutubeUrl"] == job["youtube_url"]


def test_retry_form_api_and_detail_link(client: TestClient, tmp_path: Path) -> None:
    source = "https://www.youtube.com/watch?v=retryApi1"
    job_id = _create_completed_job(
        client,
        youtube_url=source,
        stereo_width_mode="preserve",
    )

    api = client.get(f"/api/jobs/{job_id}/retry-form")
    assert api.status_code == 200
    data = api.json()
    assert data["sourceYoutubeUrl"] == source
    assert data["presetName"] == "ref.wav"
    assert data["stereoWidthMode"] == "preserve"

    detail = client.get(f"/jobs/{job_id}")
    assert f'href="/?from_job={job_id}"' in detail.text
    assert "Try again" in detail.text

    other = TestClient(fastapi_application)
    other.get("/")
    assert other.get(f"/api/jobs/{job_id}/retry-form").status_code == 404


def test_home_includes_retry_form_bootstrap(client: TestClient) -> None:
    job_id = _create_completed_job(client)
    home = client.get(f"/?from_job={job_id}")
    assert home.status_code == 200
    assert "bootstrapFormRestore" in home.text
    assert 'id="retry-form-notice"' in home.text


def test_retry_form_reflects_job_not_generic_prefs(
    client: TestClient, tmp_path: Path
) -> None:
    job_a = _create_completed_job(
        client,
        youtube_url="https://www.youtube.com/watch?v=jobA111",
        stereo_width_mode="preserve",
    )
    job_b = _create_completed_job(
        client,
        youtube_url="https://www.youtube.com/watch?v=jobB222",
        stereo_width_mode="follow_reference",
    )

    work = tmp_path / "work"
    store = JobStore(work / "remastered_jobs.db")
    job_a_row = store.get_job(job_a)
    assert job_a_row is not None

    retry_b = client.get(f"/api/jobs/{job_b}/retry-form").json()
    assert retry_b["sourceYoutubeUrl"] == "https://www.youtube.com/watch?v=jobB222"
    assert retry_b["stereoWidthMode"] == "follow_reference"

    retry_a = client.get(f"/api/jobs/{job_a}/retry-form").json()
    assert retry_a["sourceYoutubeUrl"] == "https://www.youtube.com/watch?v=jobA111"
    assert retry_a["stereoWidthMode"] == "preserve"
