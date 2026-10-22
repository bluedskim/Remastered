from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remastered.web.app import STATIC_DIR, fastapi_application
from remastered.web.static_assets import (
    compute_static_hashes,
    init_static_hashes,
    static_url,
)


def test_static_url_includes_content_hash() -> None:
    init_static_hashes(STATIC_DIR)
    url = static_url("style.css")
    assert url.startswith("/static/style.css?v=")
    version = url.split("?v=", 1)[1]
    assert re.fullmatch(r"[a-f0-9]{12}", version)


def test_compute_static_hashes_changes_when_file_changes(tmp_path: Path) -> None:
    css = tmp_path / "style.css"
    css.write_text("a { }", encoding="utf-8")
    first = compute_static_hashes(tmp_path)["style.css"]
    css.write_text("a { color: red; }", encoding="utf-8")
    second = compute_static_hashes(tmp_path)["style.css"]
    assert first != second


def test_static_url_rejects_invalid_filename() -> None:
    with pytest.raises(ValueError):
        static_url("../secrets")
    with pytest.raises(KeyError):
        static_url("missing.js")


def test_versioned_static_has_long_cache_control() -> None:
    init_static_hashes(STATIC_DIR)
    url = static_url("style.css")
    with TestClient(fastapi_application) as client:
        resp = client.get(url)
    assert resp.status_code == 200
    assert "immutable" in resp.headers.get("cache-control", "")


def test_html_home_is_not_long_cached() -> None:
    with TestClient(fastapi_application) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "").lower()
    assert "no-cache" in cc
    assert "immutable" not in cc


def test_home_page_references_versioned_css(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = tmp_path / "work"
    refs = tmp_path / "refs"
    work.mkdir()
    refs.mkdir()
    (refs / "ref.wav").write_bytes(b"x")
    monkeypatch.setenv("REMASTERED_WORK_DIR", str(work))
    monkeypatch.setenv("REMASTERED_WEB_REFERENCES_DIR", str(refs))
    with TestClient(fastapi_application) as client:
        html = client.get("/").text
    assert "/static/style.css?v=" in html
