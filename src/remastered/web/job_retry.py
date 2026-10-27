from __future__ import annotations

from pathlib import Path
from typing import Any

from remastered.stereo_width import DEFAULT_STEREO_WIDTH_MODE, VALID_STEREO_WIDTH_MODES
from remastered.web.reference_prefs import ReferencePrefs
from remastered.web.job_reference_snapshot import is_job_upload_reference_snapshot
from remastered.web.reference_youtube import is_youtube_reference_job
from remastered.web.settings import WebSettings


def build_job_retry_form_payload(
    job: dict[str, Any],
    *,
    shared_refs: list[str],
    user_refs: list[str],
    user_refs_dir: Path,
    work_dir: Path,
    ws: WebSettings,
) -> dict[str, Any]:
    """Map a stored job row to home-form prefill (client camelCase + warnings)."""
    warnings: list[str] = []
    retry_from_job_id: str | None = None
    source_youtube_url = (job.get("youtube_url") or "").strip()
    stereo_width_mode = job.get("stereo_width_mode") or DEFAULT_STEREO_WIDTH_MODE
    if stereo_width_mode not in VALID_STEREO_WIDTH_MODES:
        stereo_width_mode = DEFAULT_STEREO_WIDTH_MODE

    source_type = (job.get("reference_source_type") or "preset").strip().lower()
    if is_youtube_reference_job(job):
        source_type = "youtube"

    prefs: ReferencePrefs
    if source_type == "youtube":
        youtube_url = (job.get("reference_youtube_url") or "").strip()
        prefs = ReferencePrefs(
            tab="youtube",
            youtube_url=youtube_url,
            source_youtube_url=source_youtube_url,
        )
    elif source_type == "upload":
        snapshot = Path(job.get("reference_path") or "")
        upload_filename = snapshot.name
        tab = "upload"
        if upload_filename and is_job_upload_reference_snapshot(
            snapshot, job, work_dir
        ):
            if snapshot.is_file():
                retry_from_job_id = job["id"]
            else:
                warnings.append(
                    "The reference file used for this job is no longer available."
                )
                upload_filename = ""
                tab = "preset"
        else:
            warnings.append(
                "The reference file used for this job is no longer available."
            )
            upload_filename = ""
            tab = "preset"
        prefs = ReferencePrefs(
            tab=tab,
            upload_filename=upload_filename,
            source_youtube_url=source_youtube_url,
        )
    else:
        preset_name = Path(job.get("reference_path") or "").name
        preset_library = _preset_library_for_name(
            preset_name,
            job.get("reference_path") or "",
            shared_refs=shared_refs,
            user_refs=user_refs,
            user_refs_dir=user_refs_dir,
            shared_refs_dir=ws.references_path,
        )
        if preset_name and preset_name not in shared_refs and preset_name not in user_refs:
            ref_file = Path(job.get("reference_path") or "")
            if not ref_file.is_file():
                warnings.append(
                    "The preset reference used for this job is no longer available."
                )
                preset_name = ""
        prefs = ReferencePrefs(
            tab="preset",
            preset_name=preset_name,
            preset_library=preset_library,
            source_youtube_url=source_youtube_url,
        )

    payload = prefs.to_client_dict()
    payload["sourceYoutubeUrl"] = source_youtube_url
    payload["stereoWidthMode"] = stereo_width_mode
    payload["warnings"] = warnings
    if source_type == "upload" and retry_from_job_id:
        payload["retryFromJobId"] = retry_from_job_id
    return payload


def _preset_library_for_name(
    preset_name: str,
    reference_path: str,
    *,
    shared_refs: list[str],
    user_refs: list[str],
    user_refs_dir: Path,
    shared_refs_dir: Path,
) -> str:
    if preset_name in user_refs:
        return "user"
    if preset_name in shared_refs:
        return "shared"
    ref_file = Path(reference_path)
    if ref_file.is_file():
        try:
            ref_file.resolve().relative_to(shared_refs_dir.resolve())
            return "shared"
        except ValueError:
            pass
        try:
            ref_file.resolve().relative_to(user_refs_dir.resolve())
            return "user"
        except ValueError:
            pass
    return "shared"
