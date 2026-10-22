from __future__ import annotations

from remastered.web.auth_display import format_account_email_display


def test_format_short_email_unchanged() -> None:
    assert format_account_email_display("a@b.co") == "a@b.co"


def test_format_long_email_truncates() -> None:
    long = "verylonglocalpartname@example.com"
    out = format_account_email_display(long, max_len=20)
    assert len(out) <= 20
    assert "@" in out
