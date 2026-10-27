from __future__ import annotations

from pathlib import Path

from remastered.web.reference_prefs import (
    ReferencePrefs,
    merge_client_prefs,
    sanitize_prefs_for_owner,
    with_active_tab,
)


def test_merge_client_prefs_keeps_reference_youtube_when_switching_tab() -> None:
    existing = ReferencePrefs(
        tab="youtube",
        youtube_url="https://www.youtube.com/watch?v=abc123XYZ99",
        preset_name="Pop.wav",
    )
    incoming = ReferencePrefs(tab="preset", preset_name="Rock.wav")
    merged = merge_client_prefs(existing, incoming)
    assert merged.tab == "preset"
    assert merged.preset_name == "Rock.wav"
    assert merged.youtube_url == "https://www.youtube.com/watch?v=abc123XYZ99"


def test_with_active_tab_preserves_other_tab_fields() -> None:
    base = ReferencePrefs(
        tab="youtube",
        youtube_url="https://www.youtube.com/watch?v=abc",
        preset_name="Pop.wav",
        upload_filename="ref.wav",
    )
    merged = with_active_tab(base, "preset", preset_name="Rock.wav", preset_library="shared")
    assert merged.tab == "preset"
    assert merged.preset_name == "Rock.wav"
    assert merged.youtube_url == "https://www.youtube.com/watch?v=abc"
    assert merged.upload_filename == "ref.wav"


def test_sanitize_drops_missing_upload_file(tmp_path: Path) -> None:
    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    prefs = ReferencePrefs(tab="upload", upload_filename="gone.wav")
    out = sanitize_prefs_for_owner(
        prefs,
        shared_refs=["a.wav"],
        user_refs=[],
        user_refs_dir=refs_dir,
    )
    assert out.tab == "preset"
    assert out.upload_filename == ""


def test_sanitize_keeps_existing_upload(tmp_path: Path) -> None:
    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    (refs_dir / "mine.wav").write_bytes(b"x")
    prefs = ReferencePrefs(tab="upload", upload_filename="mine.wav")
    out = sanitize_prefs_for_owner(
        prefs,
        shared_refs=[],
        user_refs=["mine.wav"],
        user_refs_dir=refs_dir,
    )
    assert out.tab == "upload"
    assert out.upload_filename == "mine.wav"
