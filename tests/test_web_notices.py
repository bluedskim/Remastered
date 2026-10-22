from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application
from remastered.web.notices import rights_notice_lines, rights_notice_short


def test_rights_notice_helpers_use_retention_days() -> None:
    lines = rights_notice_lines(21)
    assert any("21 days" in line for line in lines)
    assert "own" in lines[0].lower() or "licensed" in lines[0].lower()
    assert str(21) in rights_notice_short(21)


@pytest.fixture
def notice_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"test")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    monkeypatch.setenv("REMASTERED_WEB_JOB_RETENTION_DAYS", "21")
    with TestClient(fastapi_application) as client:
        yield client


def test_home_shows_rights_notice_with_retention(notice_client: TestClient) -> None:
    resp = notice_client.get("/")
    assert resp.status_code == 200
    assert "notice-box" in resp.text
    assert "deleted automatically after 21 days" in resp.text
    assert "copyright" in resp.text.lower()
