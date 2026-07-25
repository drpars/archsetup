"""systemd service helpers."""

from __future__ import annotations

import subprocess

from .pacman import run


def enable(name: str) -> int:
    return run(["sudo", "systemctl", "enable", name])


def enable_now(name: str) -> int:
    return run(["sudo", "systemctl", "enable", "--now", name])


def unit_exists(name: str) -> bool:
    out = subprocess.run(
        ["systemctl", "list-unit-files", "--no-legend", name],
        capture_output=True,
        text=True,
    )
    return bool(out.stdout.strip())


def disable(name: str) -> int:
    return run(["sudo", "systemctl", "disable", name])


def enable_user_now(name: str) -> int:
    """Enable a --user unit. No sudo: user units are not managed as root."""
    rc = run(["systemctl", "--user", "daemon-reload"])
    return rc | run(["systemctl", "--user", "enable", "--now", name])
