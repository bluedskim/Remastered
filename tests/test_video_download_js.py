from __future__ import annotations

from pathlib import Path

from remastered.web.app import PACKAGE_DIR

VIDEO_DOWNLOAD_JS = Path(PACKAGE_DIR) / "static" / "video-download.js"
JOB_DELETE_JS = Path(PACKAGE_DIR) / "static" / "job-delete.js"
JOB_DETAIL_HTML = Path(PACKAGE_DIR) / "templates" / "job_detail.html"
JOB_PANEL_POLL_HTML = Path(PACKAGE_DIR) / "templates" / "partials" / "job_panel_poll.html"
JOB_PLAYERS_HTML = Path(PACKAGE_DIR) / "templates" / "partials" / "job_players.html"


def test_job_detail_loads_video_download_script() -> None:
    html = JOB_DETAIL_HTML.read_text(encoding="utf-8")
    assert "video-download.js" in html
    assert 'id="job-players"' in html
    poll = JOB_PANEL_POLL_HTML.read_text(encoding="utf-8")
    assert 'id="job-players"' in poll
    assert "hx-swap-oob" in poll


def test_job_players_partial_exposes_video_download_button() -> None:
    players = JOB_PLAYERS_HTML.read_text(encoding="utf-8")
    assert "data-video-download" in players
    assert "{% if video_download_show %}" in players


def test_video_download_js_uses_document_click_delegation() -> None:
    source = VIDEO_DOWNLOAD_JS.read_text(encoding="utf-8")
    assert "document.addEventListener(\"click\"" in source
    assert 'closest("[data-video-download]")' in source
    assert "videoDownloadBound" not in source
    assert "htmx:afterSwap" not in source
    assert "querySelectorAll(\"[data-video-download]\")" not in source


def test_job_delete_js_uses_document_click_delegation() -> None:
    source = JOB_DELETE_JS.read_text(encoding="utf-8")
    assert "document.addEventListener(\"click\"" in source
    assert 'closest("[data-job-delete]")' in source
    assert "deleteWired" not in source
    assert "htmx:oobAfterSwap" not in source


def test_job_delete_js_avoids_blocked_delete_url_segment() -> None:
    source = JOB_DELETE_JS.read_text(encoding="utf-8")
    assert 'method: "DELETE"' in source
    assert 'encodeURIComponent(jobId) + "/delete"' not in source
    assert 'encodeURIComponent(jobId) + "/remove"' in source
