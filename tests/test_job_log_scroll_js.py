from __future__ import annotations

from pathlib import Path

from remastered.web.app import PACKAGE_DIR

JOB_LOG_SCROLL_JS = Path(PACKAGE_DIR) / "static" / "job-log-scroll.js"
JOB_DETAIL_HTML = Path(PACKAGE_DIR) / "templates" / "job_detail.html"
JOB_PANEL_STATUS_HTML = (
    Path(PACKAGE_DIR) / "templates" / "partials" / "job_panel_status.html"
)


def test_job_detail_loads_log_scroll_script() -> None:
    html = JOB_DETAIL_HTML.read_text(encoding="utf-8")
    assert "job-log-scroll.js" in html


def test_log_box_has_scroll_hook_attribute() -> None:
    panel = JOB_PANEL_STATUS_HTML.read_text(encoding="utf-8")
    assert "data-job-log-box" in panel
    assert 'class="log-box"' in panel


def test_job_log_scroll_preserves_position_and_bottom_pin() -> None:
    source = JOB_LOG_SCROLL_JS.read_text(encoding="utf-8")
    assert 'htmx:beforeSwap"' in source
    assert 'htmx:afterSwap"' in source
    assert "job-status" in source
    assert "stickBottom" in source
    assert "scrollTop" in source
    assert "NEAR_BOTTOM" in source or "NEAR_BOTTOM_PX" in source


def test_job_log_scroll_stores_state_on_job_status_root() -> None:
    source = JOB_LOG_SCROLL_JS.read_text(encoding="utf-8")
    assert "jobLogScroll" in source
    assert "data-job-log-box" in source
