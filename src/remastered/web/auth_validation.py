from __future__ import annotations

import re

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9+_.-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

INVALID_EMAIL_MESSAGE = "Enter a valid email address."
PASSWORD_TOO_SHORT_MESSAGE = "Password must be at least 8 characters."

VERIFICATION_CODE_REGEX = re.compile(r"^\d{6}$")


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


def require_valid_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError(PASSWORD_TOO_SHORT_MESSAGE)
    return password
