from __future__ import annotations

from fastapi.testclient import TestClient

from remastered.web.app import STATIC_DIR, fastapi_application
from remastered.web.static_assets import init_static_hashes, static_url


def test_favicon_assets_return_200() -> None:
    init_static_hashes(STATIC_DIR)
    names = (
        "favicon.svg",
        "favicon-16.png",
        "favicon-32.png",
        "apple-touch-icon.png",
        "icon-192.png",
        "icon-512.png",
        "site.webmanifest",
    )
    with TestClient(fastapi_application) as client:
        assert client.get("/favicon.ico").status_code == 200
        for name in names:
            resp = client.get(static_url(name))
            assert resp.status_code == 200


def test_home_includes_favicon_links() -> None:
    init_static_hashes(STATIC_DIR)
    with TestClient(fastapi_application) as client:
        html = client.get("/").text
    assert 'rel="icon" type="image/svg+xml"' in html
    assert "/static/favicon.svg?v=" in html
    assert 'name="theme-color" content="#07060f"' in html
    assert "/static/site.webmanifest?v=" in html
