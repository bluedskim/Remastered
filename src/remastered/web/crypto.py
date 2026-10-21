from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    key = os.environ.get("REMASTERED_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "REMASTERED_TOKEN_ENCRYPTION_KEY 환경 변수가 필요합니다 "
            "(Fernet 키: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")"
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plain: str) -> bytes:
    return _fernet().encrypt(plain.encode("utf-8"))


def decrypt_token(blob: bytes) -> str:
    try:
        return _fernet().decrypt(blob).decode("utf-8")
    except InvalidToken:
        raise ValueError("토큰 복호화에 실패했습니다. 암호화 키를 확인하세요.")
