from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.config import resolve_settings
from remastered.web.app import fastapi_application
from remastered.web.db import JobStore


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


def _store(client: TestClient) -> JobStore:
    work_dir = resolve_settings().work_dir
    return JobStore(work_dir / "remastered_jobs.db")


def _set_created_at(store: JobStore, job_id: str, created_at: str) -> None:
    with store._conn() as conn:
        conn.execute(
            "UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?",
            (created_at, created_at, job_id),
        )


def _seed_jobs(
    store: JobStore,
    user_id: str,
    count: int,
    *,
    ref: str,
    start: datetime | None = None,
) -> list[str]:
    base = start or datetime(2024, 6, 1, tzinfo=timezone.utc)
    ids: list[str] = []
    for i in range(count):
        job_id = store.create_job(
            user_id=user_id,
            youtube_url=f"https://www.youtube.com/watch?v=pageTest{i:02d}",
            reference_path=ref,
            title=f"Job {i}",
            description="",
            privacy="private",
            upload_enabled=False,
        )
        ts = (base + timedelta(minutes=i)).isoformat()
        _set_created_at(store, job_id, ts)
        ids.append(job_id)
    return ids


def _job_ids_in_home(html: str) -> list[str]:
    return re.findall(r'data-job-delete="([a-f0-9]+)"', html)


def _anon_owner_id(client: TestClient, store: JobStore, ref: str) -> str:
    resp = client.post(
        "/jobs",
        data={
            "youtube_url": "https://www.youtube.com/watch?v=ownerProbe",
            "reference_name": Path(ref).name,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rstrip("/").split("/")[-1]
    job = store.get_job(job_id)
    assert job is not None
    owner = job["user_id"]
    store.delete_job(job_id)
    return owner


def test_home_shows_twenty_jobs_and_show_more_when_over_limit(
    client: TestClient,
    tmp_path: Path,
) -> None:
    store = _store(client)
    ref = str(tmp_path / "refs" / "ref.wav")
    anon_owner = _anon_owner_id(client, store, ref)
    job_ids = _seed_jobs(store, anon_owner, 23, ref=ref)

    home = client.get("/")
    assert home.status_code == 200
    shown = _job_ids_in_home(home.text)
    assert len(shown) == 20
    assert "Show more" in home.text
    assert "job-list-more-btn" in home.text

    oldest_three = set(job_ids[:3])
    assert not oldest_three.intersection(shown)


def test_jobs_list_api_second_page_and_no_duplicates(
    client: TestClient,
    tmp_path: Path,
) -> None:
    store = _store(client)
    ref = str(tmp_path / "refs" / "ref.wav")
    anon_owner = _anon_owner_id(client, store, ref)
    job_ids = _seed_jobs(store, anon_owner, 23, ref=ref)

    home = client.get("/")
    page_one = _job_ids_in_home(home.text)
    assert len(page_one) == 20

    match = re.search(
        r'data-before-created-at="([^"]+)"\s+data-before-id="([a-f0-9]+)"',
        home.text,
    )
    assert match is not None
    cursor_created_at, cursor_id = match.group(1), match.group(2)

    resp = client.get(
        "/api/jobs/list",
        params={"before_created_at": cursor_created_at, "before_id": cursor_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_more"] is False
    assert data["next_cursor"] is None
    page_two = re.findall(r'data-job-delete="([a-f0-9]+)"', data["html"])
    assert len(page_two) == 3

    combined = page_one + page_two
    assert len(combined) == len(set(combined))
    assert set(combined) == set(job_ids)


def test_merged_account_and_anon_jobs_paginate_without_gaps(
    tmp_path: Path,
) -> None:
    ref = str(tmp_path / "refs" / "ref.wav")
    Path(ref).parent.mkdir(parents=True, exist_ok=True)
    Path(ref).write_bytes(b"test-audio")
    store = JobStore(tmp_path / "work" / "remastered_jobs.db")
    anon_id = "anon-merge-test"
    account_id = store.create_auth_user(
        login_id="merge@example.com",
        password_hash="hash",
    )
    account_jobs = _seed_jobs(store, account_id, 12, ref=ref)
    anon_jobs = _seed_jobs(
        store,
        anon_id,
        13,
        ref=ref,
        start=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )
    all_ids = set(account_jobs) | set(anon_jobs)
    assert len(all_ids) == 25

    collected: list[str] = []
    has_more = True
    cursor_created_at: str | None = None
    cursor_id: str | None = None
    while has_more:
        page, has_more = store.list_jobs_for_access_page(
            account_id=account_id,
            anon_id=anon_id,
            limit=20,
            before_created_at=cursor_created_at,
            before_id=cursor_id,
        )
        collected.extend(j["id"] for j in page)
        if has_more:
            last = page[-1]
            cursor_created_at = last["created_at"]
            cursor_id = last["id"]

    assert len(collected) == len(set(collected))
    assert set(collected) == all_ids


def test_store_list_jobs_for_access_page(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    _seed_jobs(store, "anon-test", 22, ref="/r")

    page1, more1 = store.list_jobs_for_access_page(
        account_id=None,
        anon_id="anon-test",
        limit=20,
    )
    assert len(page1) == 20
    assert more1 is True

    last = page1[-1]
    page2, more2 = store.list_jobs_for_access_page(
        account_id=None,
        anon_id="anon-test",
        limit=20,
        before_created_at=last["created_at"],
        before_id=last["id"],
    )
    assert len(page2) == 2
    assert more2 is False
    assert {j["id"] for j in page1}.isdisjoint({j["id"] for j in page2})
