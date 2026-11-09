from __future__ import annotations

from datetime import date
from xml.etree.ElementTree import Element, SubElement, tostring

from remastered.web.settings import WebSettings

DEFAULT_SITE_ORIGIN = "https://remastered.baem.app"

SITE_DESCRIPTION = (
    "Make quiet older songs sound as loud and clear as modern tracks. "
    "Paste a YouTube link, remaster to modern loudness, and compare before and after."
)

HOME_DESCRIPTION = SITE_DESCRIPTION

# Crawlable marketing / policy pages (no auth required for GET).
PUBLIC_SITEMAP_PATHS: tuple[str, ...] = ("/", "/privacy", "/opensource")

# User-specific, auth, API, media, and operational paths.
ROBOTS_DISALLOW_PREFIXES: tuple[str, ...] = (
    "/api/",
    "/admin/",
    "/auth/",
    "/jobs/",
    "/media/",
    "/login",
    "/signup",
    "/logout",
    "/oauth/",
    "/dashboard",
    "/health",
)


def site_origin(ws: WebSettings | None = None) -> str:
    settings = ws or WebSettings()
    raw = (settings.public_base_url or "").strip().rstrip("/")
    if not raw or "localhost" in raw or "127.0.0.1" in raw:
        return DEFAULT_SITE_ORIGIN
    return raw


def absolute_url(path: str, ws: WebSettings | None = None) -> str:
    origin = site_origin(ws)
    if not path.startswith("/"):
        path = "/" + path
    return origin + path


def build_robots_txt(ws: WebSettings | None = None) -> str:
    lines = ["User-agent: *", "Allow: /"]
    for prefix in ROBOTS_DISALLOW_PREFIXES:
        lines.append(f"Disallow: {prefix}")
    lines.append(f"Sitemap: {absolute_url('/sitemap.xml', ws)}")
    return "\n".join(lines) + "\n"


def build_sitemap_xml(ws: WebSettings | None = None) -> bytes:
    origin = site_origin(ws)
    urlset = Element(
        "urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
    )
    today = date.today().isoformat()
    for path in PUBLIC_SITEMAP_PATHS:
        url_el = SubElement(urlset, "url")
        loc = SubElement(url_el, "loc")
        loc.text = origin + (path if path != "/" else "/")
        SubElement(url_el, "lastmod").text = today
        if path == "/":
            SubElement(url_el, "changefreq").text = "weekly"
            SubElement(url_el, "priority").text = "1.0"
        else:
            SubElement(url_el, "changefreq").text = "monthly"
            SubElement(url_el, "priority").text = "0.5"
    return tostring(urlset, encoding="utf-8", xml_declaration=True)


def home_json_ld(ws: WebSettings | None = None) -> dict:
    origin = site_origin(ws)
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "name": "Remastered",
                "url": origin + "/",
                "description": SITE_DESCRIPTION,
            },
            {
                "@type": "WebApplication",
                "name": "Remastered",
                "url": origin + "/",
                "applicationCategory": "MultimediaApplication",
                "operatingSystem": "Web",
                "description": SITE_DESCRIPTION,
            },
        ],
    }


def web_page_json_ld(
    *,
    name: str,
    path: str,
    description: str,
    ws: WebSettings | None = None,
) -> dict:
    origin = site_origin(ws)
    return {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": name,
        "url": origin + path,
        "description": description,
        "isPartOf": {
            "@type": "WebSite",
            "name": "Remastered",
            "url": origin + "/",
        },
    }
