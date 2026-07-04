"""User configuration (~/.config/archsetup/config.toml)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path


def config_dir() -> Path:
    override = os.environ.get("ARCHSETUP_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".config" / "archsetup"


def config_file() -> Path:
    return config_dir() / "config.toml"


def load() -> dict:
    try:
        with open(config_file(), "rb") as fh:
            return tomllib.load(fh)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return {}


def save(conf: dict) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in conf.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key} = {value}")
        else:
            lines.append(f'{key} = "{value}"')
    config_file().write_text("\n".join(lines) + "\n", encoding="utf-8")
