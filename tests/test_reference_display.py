from __future__ import annotations

from remastered.web.reference_display import (
    build_reference_options,
    reference_display_for_job,
    reference_display_meta,
)
from remastered.web.job_ui import enrich_job_ui


def test_reference_display_meta_parses_genre_artist_title() -> None:
    meta = reference_display_meta("Pop - NewJeans - Super Shy.wav")
    assert meta["genre"] == "Pop"
    assert "NewJeans" in meta["label"]


def test_reference_display_for_job_uses_basename_only() -> None:
    meta = reference_display_for_job("/data/users/u1/references/my-upload.wav")
    assert meta["label"] == "my-upload"
    assert "users" not in meta["label"]
    assert "/" not in meta["label"]


def test_reference_display_for_job_parses_genre_from_filename() -> None:
    meta = reference_display_for_job("/references/Pop - Artist - Title.wav")
    assert meta["genre"] == "Pop"
    assert meta["label"] == "Artist — Title"


def test_enrich_job_ui_adds_reference_fields() -> None:
    job = enrich_job_ui(
        {
            "reference_path": "/references/Pop - A - B.wav",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00+00:00",
            "stage_state": {},
        }
    )
    assert job["reference_genre"] == "Pop"
    assert job["reference_label"] == "A — B"


def test_build_reference_options_merges_lists() -> None:
    opts = build_reference_options(["ref.wav"], ["mine.wav"])
    assert len(opts) == 2
    assert opts[0]["source"] == "shared"
    assert opts[1]["source"] == "user"
