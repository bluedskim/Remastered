from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application


@pytest.fixture
def home_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"test")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    with TestClient(fastapi_application) as client:
        yield client


def test_home_hero_outcome_copy(home_client: TestClient) -> None:
    resp = home_client.get("/")
    assert resp.status_code == 200
    assert "Make old, quiet songs sound loud and clear" in resp.text
    assert "Paste a YouTube link" in resp.text
    assert "YouTube audio remaster" not in resp.text
    assert "the sound you want to match" in resp.text
    assert "Loud, clear remasters from YouTube links" in resp.text


def test_home_rights_notice_collapsible(home_client: TestClient) -> None:
    resp = home_client.get("/")
    assert "notice-box--compact" in resp.text
    assert "<details" in resp.text
    assert "Only master tracks you own" in resp.text


def test_home_demo_player_hidden_without_samples(home_client: TestClient) -> None:
    resp = home_client.get("/")
    assert "Hear the difference" not in resp.text
    assert "data-ab-compare" not in resp.text.split("My jobs")[0]


def test_home_demo_player_renders_when_configured(
    home_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remastered.web.home_demo_samples import HomeDemoSamples

    monkeypatch.setattr(
        "remastered.web.app.resolve_home_demo_samples",
        lambda: HomeDemoSamples(
            before_static_path="samples/before.m4a",
            after_static_path="samples/after.m4a",
        ),
    )
    resp = home_client.get("/")
    assert "Hear the difference" in resp.text
    assert 'data-ab-compare' in resp.text
    assert "data-ab-loudness-attenuation" not in resp.text
    assert "samples/before.m4a" in resp.text
    assert "ab-player.js" in resp.text
