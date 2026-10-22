from __future__ import annotations

from remastered.web.auth_redirect import (
    login_url,
    post_auth_redirect_target,
    safe_next_path,
    signup_url,
)


def test_safe_next_path_allows_relative() -> None:
    assert safe_next_path("/jobs/abc") == "/jobs/abc"
    assert safe_next_path("  /jobs/x  ") == "/jobs/x"


def test_safe_next_path_rejects_open_redirects() -> None:
    assert safe_next_path("//evil.com") is None
    assert safe_next_path("https://evil.com") is None
    assert safe_next_path("/\\evil") is None
    assert safe_next_path("jobs/no-leading-slash") is None


def test_post_auth_redirect_target() -> None:
    assert post_auth_redirect_target("/jobs/j1") == "/jobs/j1"
    assert post_auth_redirect_target("//x") == "/"
    assert post_auth_redirect_target(None) == "/"


def test_auth_urls_preserve_next() -> None:
    assert signup_url("/jobs/j1") == "/signup?next=%2Fjobs%2Fj1"
    assert login_url("/jobs/j1") == "/login?next=%2Fjobs%2Fj1"
