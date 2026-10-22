from __future__ import annotations

import re

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9+_.-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
NICKNAME_REGEX = re.compile(r"^[a-zA-Z0-9_\uAC00-\uD7A3]{2,12}$")

INVALID_EMAIL_MESSAGE = "올바른 이메일 주소를 입력해 주세요."
INVALID_NICKNAME_MESSAGE = "닉네임은 2~12자의 영문, 숫자, 밑줄, 한글만 사용할 수 있습니다."
PASSWORD_TOO_SHORT_MESSAGE = "비밀번호는 8자 이상이어야 합니다."


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    normalized = normalize_email(email)
    return bool(normalized) and EMAIL_REGEX.match(normalized) is not None


def require_valid_email(email: str) -> str:
    normalized = normalize_email(email)
    if not is_valid_email(normalized):
        raise ValueError(INVALID_EMAIL_MESSAGE)
    return normalized


def normalize_nickname(raw: str) -> str:
    return raw.strip()


def is_valid_nickname(raw: str) -> bool:
    nickname = normalize_nickname(raw)
    return bool(nickname) and NICKNAME_REGEX.match(nickname) is not None


def require_valid_nickname(raw: str) -> str:
    nickname = normalize_nickname(raw)
    if not is_valid_nickname(nickname):
        raise ValueError(INVALID_NICKNAME_MESSAGE)
    return nickname


def require_valid_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError(PASSWORD_TOO_SHORT_MESSAGE)
    return password


VERIFICATION_CODE_REGEX = re.compile(r"^\d{6}$")
