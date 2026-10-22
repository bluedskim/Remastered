from __future__ import annotations

from remastered.web.reference_display import build_reference_options, reference_display_meta


def test_reference_display_meta_parses_genre_artist_title() -> None:
    meta = reference_display_meta("Pop - NewJeans - Super Shy.wav")
    assert meta["genre"] == "Pop"
    assert "NewJeans" in meta["label"]


def test_build_reference_options_merges_lists() -> None:
    opts = build_reference_options(["ref.wav"], ["mine.wav"])
    assert len(opts) == 2
    assert opts[0]["source"] == "shared"
    assert opts[1]["source"] == "user"
