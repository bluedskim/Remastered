from __future__ import annotations

from pathlib import Path

from remastered.web.reference_youtube import is_youtube_reference_job
from remastered.web.youtube_meta import youtube_reference_thumbnail_url


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


def reference_ui_for_job(job: dict) -> dict[str, str | None]:
    """UI fields for job reference display (preset, upload, or YouTube)."""
    source_type = (job.get("reference_source_type") or "preset").strip()
    if source_type == "youtube" or is_youtube_reference_job(job):
        video_id = (job.get("reference_video_id") or "").strip()
        title = (job.get("reference_youtube_title") or "").strip()
        url = (job.get("reference_youtube_url") or "").strip()
        label = title or (f"YouTube video {video_id}" if video_id else "YouTube video")
        thumb = youtube_reference_thumbnail_url(video_id) if video_id else None
        return {
            "kind": "youtube",
            "genre": "YouTube",
            "label": label,
            "url": url or None,
            "thumbnail_url": thumb,
            "author_name": None,
        }
    if source_type == "upload":
        ref = reference_display_for_job(job.get("reference_path"))
        return {"kind": "upload", "genre": ref["genre"], "label": ref["label"], "url": None}
    ref = reference_display_for_job(job.get("reference_path"))
    return {"kind": "preset", "genre": ref["genre"], "label": ref["label"], "url": None}


def build_reference_options(shared: list[str], user: list[str]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for name in shared:
        meta = reference_display_meta(name)
        options.append({"name": name, "source": "shared", **meta})
    for name in user:
        meta = reference_display_meta(name)
        options.append({"name": name, "source": "user", **meta})
    return options
