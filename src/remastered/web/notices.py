from __future__ import annotations


def rights_notice_lines(retention_days: int) -> list[str]:
    days = max(0, int(retention_days))
    return [
        "본인이 권리를 가진 곡이거나 사용 허락을 받은 곡만 마스터링해 주세요.",
        "마스터링 결과물의 저작권은 원저작권자에게 있으며, 무단 배포·상업적 이용 책임은 사용자에게 있습니다.",
        f"업로드된 파일과 결과물은 {days}일 후 자동 삭제됩니다.",
    ]


def rights_notice_short(retention_days: int) -> str:
    days = max(0, int(retention_days))
    return (
        "본인이 권리를 가진 곡 또는 사용 허락을 받은 곡만 이용해 주세요. "
        "저작권·무단 배포·상업적 이용 책임은 사용자에게 있으며, "
        f"파일은 {days}일 후 자동 삭제됩니다."
    )
