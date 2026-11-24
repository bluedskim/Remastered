from __future__ import annotations


def rights_notice_lines(days: int, *, retention_exempt: bool = False) -> list[str]:
    if retention_exempt:
        return [
            "Only master tracks you own or are licensed to use.",
            "Copyright in the result remains with the rights holder; you are responsible for distribution and commercial use.",
            "Uploaded files and outputs are kept until you delete them.",
        ]
    return [
        "Only master tracks you own or are licensed to use.",
        "Copyright in the result remains with the rights holder; you are responsible for distribution and commercial use.",
        f"Uploaded files and outputs are deleted automatically after {days} days.",
    ]


def rights_notice_short(days: int, *, retention_exempt: bool = False) -> str:
    if retention_exempt:
        return (
            "Use only music you have rights to. You are responsible for copyright and unauthorized use; "
            "files are kept until you delete them."
        )
    return (
        "Use only music you have rights to. You are responsible for copyright and unauthorized use; "
        f"files are deleted after {days} days."
    )
