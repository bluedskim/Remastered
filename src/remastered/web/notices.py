from __future__ import annotations


def rights_notice_lines(days: int) -> list[str]:
    return [
        "Only master tracks you own or are licensed to use.",
        "Copyright in the result remains with the rights holder; you are responsible for distribution and commercial use.",
        f"Uploaded files and outputs are deleted automatically after {days} days.",
    ]


def rights_notice_short(days: int) -> str:
    return (
        "Use only music you have rights to. You are responsible for copyright and unauthorized use; "
        f"files are deleted after {days} days."
    )
