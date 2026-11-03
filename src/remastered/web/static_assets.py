from __future__ import annotations

import hashlib
import re
from pathlib import Path

_HASH_LEN = 12
_FILENAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")

_hashes: dict[str, str] = {}


def compute_static_hashes(static_dir: Path) -> dict[str, str]:
    """Content hash (short sha256 prefix) per file in the static directory."""
    out: dict[str, str] = {}
    if not static_dir.is_dir():
        return out
    for path in sorted(static_dir.iterdir()):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:_HASH_LEN]
        out[path.name] = digest
    return out


def init_static_hashes(static_dir: Path) -> dict[str, str]:
    global _hashes
    _hashes = compute_static_hashes(static_dir)
    return dict(_hashes)


def get_static_hashes() -> dict[str, str]:
    return dict(_hashes)


def static_url(filename: str) -> str:
    """Versioned URL for a file in /static (e.g. style.css -> /static/style.css?v=abc)."""
    if filename != Path(filename).name or not _FILENAME_RE.fullmatch(filename):
        raise ValueError(f"invalid static filename: {filename}")
    name = filename
    version = _hashes.get(name)
    if not version:
        raise KeyError(f"unknown static asset: {filename}")
    return f"/static/{name}?v={version}"


def static_path_url(relpath: str) -> str:
    """URL for nested files under /static (e.g. samples/demo.m4a). Not content-versioned."""
    rel = Path((relpath or "").strip().replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"invalid static path: {relpath}")
    if not rel.parts or not all(_FILENAME_RE.fullmatch(part) for part in rel.parts):
        raise ValueError(f"invalid static path: {relpath}")
    return f"/static/{rel.as_posix()}"


def is_versioned_static_request(path: str, query_string: bytes, hashes: dict[str, str]) -> bool:
    if not path.startswith("/static/"):
        return False
    name = Path(path).name
    expected = hashes.get(name)
    if not expected:
        return False
    from urllib.parse import parse_qs

    params = parse_qs(query_string.decode("latin-1", errors="replace"))
    supplied = params.get("v", [None])[0]
    return supplied == expected
