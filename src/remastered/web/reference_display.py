from __future__ import annotations

from pathlib import Path


def reference_display_meta(filename: str) -> dict[str, str]:
    """Derive genre badge and display label from a reference filename stem."""
    stem = Path(filename).stem
    parts = [p.strip() for p in stem.split(" - ") if p.strip()]
    if len(parts) >= 3:
        return {"genre": parts[0], "label": f"{parts[1]} — {parts[2]}"}
    if len(parts) == 2:
        return {"genre": "Reference", "label": f"{parts[0]} — {parts[1]}"}
    return {"genre": "Reference", "label": stem or filename}


def reference_display_for_job(reference_path: str | None) -> dict[str, str]:
    """Friendly reference label for a job; never includes directory paths."""
    name = Path(reference_path or "").name
    if not name:
        return {"genre": "Reference", "label": "—"}
    return reference_display_meta(name)


def build_reference_options(shared: list[str], user: list[str]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for name in shared:
        meta = reference_display_meta(name)
        options.append({"name": name, "source": "shared", **meta})
    for name in user:
        meta = reference_display_meta(name)
        options.append({"name": name, "source": "user", **meta})
    return options
