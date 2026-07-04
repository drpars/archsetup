"""systemd service helpers."""

from __future__ import annotations

from .pacman import run


def enable(name: str) -> int:
    return run(["sudo", "systemctl", "enable", name])


def disable(name: str) -> int:
    return run(["sudo", "systemctl", "disable", name])
