from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import tomllib
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PrivacyStatus = Literal["private", "unlisted", "public"]
BitDepth = Literal[16, 24]


class RemasteredSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REMASTERED_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    work_dir: Path = Field(default_factory=lambda: Path.home() / ".cache" / "remastered")
    client_secrets: Path | None = None
    token_path: Path = Field(
        default_factory=lambda: Path.home() / ".config" / "remastered" / "token.json"
    )
    default_privacy: PrivacyStatus = "private"
    bit_depth: BitDepth = 16
    preview: bool = False
    ffmpeg_path: str = "ffmpeg"
    yt_dlp_path: str | None = None
    upload_category_id: str = "10"

    @field_validator("work_dir", "token_path", mode="before")
    @classmethod
    def expand_path(cls, value: Any) -> Any:
        if value is None or isinstance(value, Path):
            return value
        return Path(os.path.expanduser(str(value)))

    @field_validator("client_secrets", mode="before")
    @classmethod
    def expand_optional_path(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, Path):
            return value
        return Path(os.path.expanduser(str(value)))


def load_toml_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    section = data.get("remastered", data)
    return {k: v for k, v in section.items() if isinstance(k, str)}


def resolve_settings(
    config_file: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> RemasteredSettings:
    file_values: dict[str, Any] = {}
    if config_file and config_file.is_file():
        file_values = load_toml_config(config_file)
    elif config_file is None:
        default_path = Path.home() / ".config" / "remastered" / "config.toml"
        if default_path.is_file():
            file_values = load_toml_config(default_path)
    merged = {**file_values, **(overrides or {})}
    return RemasteredSettings(**merged)
