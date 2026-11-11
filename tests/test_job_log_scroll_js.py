from __future__ import annotations

from pathlib import Path

from remastered.web.app import PACKAGE_DIR

FIXTURES = Path(__file__).resolve().parent / "fixtures"
JOB_LOG_SCROLL_JS = Path(PACKAGE_DIR) / "static" / "job-log-scroll.js"
JOB_DETAIL_HTML = Path(PACKAGE_DIR) / "templates" / "job_detail.html"
JOB_PANEL_LOG_HTML = (
    Path(PACKAGE_DIR) / "templates" / "partials" / "job_panel_log.html"
)
JOB_PANEL_POLL_HTML = (
    Path(PACKAGE_DIR) / "templates" / "partials" / "job_panel_poll.html"
)


def test_job_detail_loads_log_scroll_script() -> None:
    html = JOB_DETAIL_HTML.read_text(encoding="utf-8")
    assert "job-log-scroll.js" in html


def test_log_box_has_scroll_hook_attribute() -> None:
    panel = JOB_PANEL_LOG_HTML.read_text(encoding="utf-8")
    assert "data-job-log-box" in panel
    assert 'class="log-box"' in panel


def test_job_log_scroll_preserves_position_and_bottom_pin() -> None:
    source = JOB_LOG_SCROLL_JS.read_text(encoding="utf-8")
    assert 'htmx:beforeSwap"' in source
    assert 'htmx:oobBeforeSwap"' in source
    assert 'htmx:oobAfterSwap"' in source
    assert 'htmx:afterSettle"' in source
    assert "job-status-log" in source
    assert "job-status" in source
    assert "stickBottom" in source
    assert "scrollTop" in source
    assert "NEAR_BOTTOM" in source or "NEAR_BOTTOM_PX" in source


def test_job_log_scroll_stores_state_on_job_status_log_root() -> None:
    source = JOB_LOG_SCROLL_JS.read_text(encoding="utf-8")
    assert "jobLogScroll" in source
    assert "data-job-log-box" in source
    assert "job-status-log" in source
    assert "captureLogScroll" in source


def test_job_detail_places_memo_between_status_and_log() -> None:
    html = JOB_DETAIL_HTML.read_text(encoding="utf-8")
    assert 'id="job-status-log"' in html
    assert "partials/job_memo.html" in html
    memo_pos = html.index("partials/job_memo.html")
    log_pos = html.index('id="job-status-log"')
    status_pos = html.index('id="job-status"')
    assert status_pos < memo_pos < log_pos


def test_panel_poll_oob_updates_log_on_terminal_response() -> None:
    """Log OOB uses same hx-swap-oob path as actions/players (still in poll body at job end)."""
    poll = JOB_PANEL_POLL_HTML.read_text(encoding="utf-8")
    assert 'id="job-status-log" hx-swap-oob="innerHTML"' in poll


def test_htmx_2_0_4_oob_swap_fires_oob_events_not_primary_before_swap() -> None:
    excerpt = (FIXTURES / "htmx-2.0.4-oob-swap.excerpt.js").read_text(encoding="utf-8")
    assert "htmx:oobBeforeSwap" in excerpt
    assert "htmx:oobAfterSwap" in excerpt
    assert "htmx:beforeSwap" not in excerpt
