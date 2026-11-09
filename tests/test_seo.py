from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from remastered.web.app import fastapi_application
from remastered.web.seo import (
    PUBLIC_SITEMAP_PATHS,
    ROBOTS_DISALLOW_PREFIXES,
    SITE_DESCRIPTION,
    build_robots_txt,
    build_sitemap_xml,
)


def test_robots_txt_unauthenticated_200_and_content() -> None:
    with TestClient(fastapi_application) as client:
        resp = client.get("/robots.txt")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        body = resp.text
        assert "User-agent: *" in body
        assert "Allow: /" in body
        for prefix in ROBOTS_DISALLOW_PREFIXES:
            assert f"Disallow: {prefix}" in body
        assert "Sitemap: https://remastered.baem.app/sitemap.xml" in body
    assert "Disallow: /jobs/" in build_robots_txt()


def test_sitemap_xml_unauthenticated_200_public_urls_only() -> None:
    with TestClient(fastapi_application) as client:
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert "xml" in resp.headers.get("content-type", "")
        root = ET.fromstring(resp.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [
            el.text.rstrip("/") if el.text and el.text != "https://remastered.baem.app/" else el.text
            for el in root.findall(".//sm:loc", ns)
        ]
        assert len(locs) == len(PUBLIC_SITEMAP_PATHS)
        assert all(u.startswith("https://remastered.baem.app") for u in locs)
        joined = resp.text
        assert "/jobs/" not in joined
        assert "/media/" not in joined
        assert "/api/" not in joined
        for path in ("/privacy", "/opensource"):
            assert path in joined


def test_favicon_and_seo_routes_no_auth_redirect() -> None:
    with TestClient(fastapi_application) as client:
        for path in ("/robots.txt", "/sitemap.xml", "/favicon.ico"):
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code == 200, path


def test_home_meta_og_twitter_and_json_ld() -> None:
    with TestClient(fastapi_application) as client:
        html = client.get("/").text
    assert SITE_DESCRIPTION.split(".")[0] in html
    assert 'property="og:title"' in html
    assert 'property="og:description"' in html
    assert 'property="og:url"' in html
    assert 'name="twitter:card"' in html
    assert 'rel="canonical"' in html
    assert "<noscript>" in html
    assert "Make old, quiet songs sound loud and clear" in html
    match = re.search(
        r'<script type="application/ld\+json">(\{.*?\})</script>',
        html,
        re.DOTALL,
    )
    assert match
    data = json.loads(match.group(1))
    assert data["@context"] == "https://schema.org"
    graph = data.get("@graph") or [data]
    types = {node.get("@type") for node in graph if isinstance(node, dict)}
    assert "WebApplication" in types or data.get("@type") == "WebApplication"
    assert "WebSite" in types or data.get("@type") == "WebSite"


def test_privacy_has_webpage_json_ld() -> None:
    with TestClient(fastapi_application) as client:
        html = client.get("/privacy").text
    match = re.search(
        r'<script type="application/ld\+json">(\{.*?\})</script>',
        html,
        re.DOTALL,
    )
    assert match
    data = json.loads(match.group(1))
    assert data.get("@type") == "WebPage"
    assert data.get("url", "").endswith("/privacy")
