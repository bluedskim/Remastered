from __future__ import annotations

from remastered.youtube_url import extract_youtube_video_id, normalize_youtube_url

VID = "Y0a0QuLEC8A"


def test_normalize_strips_playlist_and_radio_params() -> None:
    raw = (
        f"https://www.youtube.com/watch?v={VID}"
        "&list=RDY0a0QuLEC8A&start_radio=1&index=3&si=abc"
    )
    assert normalize_youtube_url(raw) == f"https://www.youtube.com/watch?v={VID}"


def test_normalize_youtu_be() -> None:
    assert (
        normalize_youtube_url(f"https://youtu.be/{VID}?si=tracking")
        == f"https://www.youtube.com/watch?v={VID}"
    )


def test_normalize_shorts_and_music() -> None:
    assert (
        normalize_youtube_url(f"https://www.youtube.com/shorts/{VID}")
        == f"https://www.youtube.com/watch?v={VID}"
    )
    assert (
        normalize_youtube_url(f"https://music.youtube.com/watch?v={VID}&list=PLfoo")
        == f"https://www.youtube.com/watch?v={VID}"
    )


def test_normalize_preserves_t() -> None:
    assert (
        normalize_youtube_url(f"https://www.youtube.com/watch?v={VID}&t=42s&list=RD")
        == f"https://www.youtube.com/watch?v={VID}&t=42s"
    )


def test_extract_video_id_unknown_url_returns_none() -> None:
    assert extract_youtube_video_id("https://example.com/not-youtube") is None
    assert normalize_youtube_url("https://example.com/not-youtube") == (
        "https://example.com/not-youtube"
    )
