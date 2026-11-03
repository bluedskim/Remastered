"""Optional before/after demo audio on the home page (not shipped by default)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from remastered.web.settings import WebSettings

_WEB_DIR = Path(__file__).resolve().parent
_STATIC_ROOT = _WEB_DIR / "static"

_DEMO_EXTENSIONS = (".m4a", ".mp3", ".wav", ".aac", ".flac")


@dataclass(frozen=True)
class HomeDemoSamples:
    before_static_path: str
    after_static_path: str


def _static_samples_dir() -> Path:
    return _STATIC_ROOT / "samples"


def _resolve_env_path(raw: str, *, static_root: Path) -> Path | None:
    value = (raw or "").strip()
    if not value:
        return None
    path = Path(os.path.expanduser(value))
    if not path.is_absolute():
        path = static_root / value
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    try:
        resolved = path.resolve()
        resolved.relative_to(static_root.resolve())
    except ValueError:
        return None
    return resolved


def _path_to_static_relpath(path: Path, static_root: Path) -> str:
    rel = path.resolve().relative_to(static_root.resolve())
    return rel.as_posix()


def _find_stem_in_samples(stem: str, static_root: Path) -> str | None:
    samples = _static_samples_dir()
    if not samples.is_dir():
        return None
    for ext in _DEMO_EXTENSIONS:
        candidate = samples / f"{stem}{ext}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return _path_to_static_relpath(candidate, static_root)
    return None


def resolve_home_demo_samples() -> HomeDemoSamples | None:
    """Return static-relative paths for before/after demo audio when both exist."""
    static_root = _STATIC_ROOT
    ws = WebSettings()
    before_env = os.environ.get("REMASTERED_WEB_HOME_DEMO_BEFORE") or ws.home_demo_before
    after_env = os.environ.get("REMASTERED_WEB_HOME_DEMO_AFTER") or ws.home_demo_after

    before_rel: str | None = None
    after_rel: str | None = None

    if before_env and after_env:
        before_path = _resolve_env_path(before_env, static_root=static_root)
        after_path = _resolve_env_path(after_env, static_root=static_root)
        if before_path and after_path:
            try:
                before_rel = _path_to_static_relpath(before_path, static_root)
                after_rel = _path_to_static_relpath(after_path, static_root)
            except ValueError:
                return None
    else:
        before_rel = _find_stem_in_samples("before", static_root)
        after_rel = _find_stem_in_samples("after", static_root)

    if not before_rel or not after_rel:
        return None
    return HomeDemoSamples(before_static_path=before_rel, after_static_path=after_rel)
