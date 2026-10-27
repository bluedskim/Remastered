from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REFERENCE_PREF_TABS = frozenset({"preset", "youtube", "upload"})


@dataclass
class ReferencePrefs:
    tab: str = "preset"
    preset_name: str = ""
    preset_library: str = "shared"
    youtube_url: str = ""
    upload_filename: str = ""

    def to_client_dict(self) -> dict[str, str]:
        return {
            "tab": self.tab,
            "presetName": self.preset_name,
            "presetLibrary": self.preset_library,
            "youtubeUrl": self.youtube_url,
            "uploadFilename": self.upload_filename,
        }

    @classmethod
    def from_client_dict(cls, data: dict[str, Any]) -> ReferencePrefs | None:
        if not data or not isinstance(data, dict):
            return None
        tab = str(data.get("tab") or "preset").strip().lower()
        if tab not in REFERENCE_PREF_TABS:
            tab = "preset"
        preset_library = str(data.get("presetLibrary") or "shared").strip()
        if preset_library not in ("shared", "user"):
            preset_library = "shared"
        return cls(
            tab=tab,
            preset_name=str(data.get("presetName") or "").strip(),
            preset_library=preset_library,
            youtube_url=str(data.get("youtubeUrl") or "").strip(),
            upload_filename=Path(str(data.get("uploadFilename") or "")).name,
        )


def parse_reference_prefs_json(raw: str | None) -> ReferencePrefs | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return ReferencePrefs(
        tab=_norm_tab(data.get("tab")),
        preset_name=str(data.get("preset_name") or "").strip(),
        preset_library=_norm_library(data.get("preset_library")),
        youtube_url=str(data.get("youtube_url") or "").strip(),
        upload_filename=Path(str(data.get("upload_filename") or "")).name,
    )


def reference_prefs_to_json(prefs: ReferencePrefs) -> str:
    return json.dumps(
        {
            "tab": prefs.tab,
            "preset_name": prefs.preset_name,
            "preset_library": prefs.preset_library,
            "youtube_url": prefs.youtube_url,
            "upload_filename": prefs.upload_filename,
        }
    )


def _norm_tab(value: Any) -> str:
    tab = str(value or "preset").strip().lower()
    return tab if tab in REFERENCE_PREF_TABS else "preset"


def _norm_library(value: Any) -> str:
    lib = str(value or "shared").strip()
    return lib if lib in ("shared", "user") else "shared"


def with_active_tab(
    base: ReferencePrefs | None,
    tab: str,
    *,
    preset_name: str | None = None,
    preset_library: str | None = None,
    youtube_url: str | None = None,
    upload_filename: str | None = None,
) -> ReferencePrefs:
    """Update the active tab and only the fields explicitly passed."""
    cur = base or ReferencePrefs()
    return ReferencePrefs(
        tab=tab,
        preset_name=preset_name if preset_name is not None else cur.preset_name,
        preset_library=preset_library if preset_library is not None else cur.preset_library,
        youtube_url=youtube_url if youtube_url is not None else cur.youtube_url,
        upload_filename=upload_filename if upload_filename is not None else cur.upload_filename,
    )


def prefs_from_store_row(row: dict[str, str] | None) -> ReferencePrefs | None:
    if not row:
        return None
    return ReferencePrefs(
        tab=row["tab"],
        preset_name=row["preset_name"],
        preset_library=row["preset_library"],
        youtube_url=row["youtube_url"],
        upload_filename=row["upload_filename"],
    )


def sanitize_prefs_for_owner(
    prefs: ReferencePrefs,
    *,
    shared_refs: list[str],
    user_refs: list[str],
    user_refs_dir: Path,
) -> ReferencePrefs:
    """Clear missing upload files; fall back active tab if upload is empty."""
    tab = prefs.tab
    upload_filename = prefs.upload_filename
    if upload_filename:
        path = user_refs_dir / upload_filename
        if not path.is_file():
            upload_filename = ""
    if tab == "upload" and not upload_filename:
        tab = "preset"
    return ReferencePrefs(
        tab=tab,
        preset_name=prefs.preset_name,
        preset_library=prefs.preset_library,
        youtube_url=prefs.youtube_url,
        upload_filename=upload_filename,
    )
