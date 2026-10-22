#!/usr/bin/env python3
"""Generate PNG/ICO favicons and site.webmanifest from favicon.svg design."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "remastered" / "web" / "static"

VIOLET = (139, 92, 246)
PINK = (236, 72, 153)
LIGHT_PINK = (244, 114, 182)
BG_DARK = (7, 6, 15)


def _mix(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _gradient_rgb(t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    if t <= 0.55:
        return _mix(VIOLET, PINK, t / 0.55)
    return _mix(PINK, LIGHT_PINK, (t - 0.55) / 0.45)


def _inside_rounded_rect(x: float, y: float, size: float, radius: float) -> bool:
    if x < 0 or y < 0 or x >= size or y >= size:
        return False
    r = radius
    if x < r and y < r:
        return (x - r) ** 2 + (y - r) ** 2 <= r * r
    if x > size - r and y < r:
        return (x - (size - r)) ** 2 + (y - r) ** 2 <= r * r
    if x < r and y > size - r:
        return (x - r) ** 2 + (y - (size - r)) ** 2 <= r * r
    if x > size - r and y > size - r:
        return (x - (size - r)) ** 2 + (y - (size - r)) ** 2 <= r * r
    return True


def _inside_diamond(px: float, py: float, cx: float, cy: float, half: float) -> bool:
    return abs(px - cx) / half + abs(py - cy) / half <= 1.0


def render_tile(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    radius = size * (7.5 / 32.0)
    cx = size / 2.0
    cy = size / 2.0
    diamond_half = size * 0.28

    pixels = img.load()
    for y in range(size):
        for x in range(size):
            if not _inside_rounded_rect(x + 0.5, y + 0.5, size, radius):
                continue
            t = (x / max(size - 1, 1) + y / max(size - 1, 1)) / 2.0
            r, g, b = _gradient_rgb(t)
            if _inside_diamond(x + 0.5, y + 0.5, cx, cy, diamond_half):
                pixels[x, y] = (255, 255, 255, 255)
            else:
                pixels[x, y] = (r, g, b, 255)
    return img


def render_on_solid(canvas: int, tile: int, bg: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (canvas, canvas), bg + (255,))
    icon = render_tile(tile)
    offset = (canvas - tile) // 2
    img.paste(icon, (offset, offset), icon)
    return img


def write_manifest(hashes: dict[str, str]) -> None:
    def url(name: str) -> str:
        return f"/static/{name}?v={hashes[name]}"

    manifest = {
        "name": "Remastered",
        "short_name": "Remastered",
        "display": "standalone",
        "background_color": "#07060f",
        "theme_color": "#07060f",
        "icons": [
            {
                "src": url("icon-192.png"),
                "sizes": "192x192",
                "type": "image/png",
            },
            {
                "src": url("icon-512.png"),
                "sizes": "512x512",
                "type": "image/png",
            },
        ],
    }
    (STATIC / "site.webmanifest").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    STATIC.mkdir(parents=True, exist_ok=True)

    sizes = {
        "favicon-16.png": 16,
        "favicon-32.png": 32,
        "icon-192.png": 192,
        "icon-512.png": 512,
    }
    for name, px in sizes.items():
        render_tile(px).save(STATIC / name, format="PNG", optimize=True)

    render_on_solid(180, 152, BG_DARK).save(
        STATIC / "apple-touch-icon.png", format="PNG", optimize=True
    )

    ico_images = [render_tile(s) for s in (16, 32, 48)]
    ico_images[0].save(
        STATIC / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=ico_images[1:],
    )

    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from remastered.web.static_assets import compute_static_hashes

    hashes = compute_static_hashes(STATIC)
    write_manifest(hashes)
    print("Wrote favicons to", STATIC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
