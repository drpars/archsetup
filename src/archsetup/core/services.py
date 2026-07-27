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


def is_active(name: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", name]).returncode == 0


def disable(name: str) -> int:
    return run(["sudo", "systemctl", "disable", name])


def enable_user_now(name: str) -> int:
    """Enable a --user unit. No sudo: user units are not managed as root.

    ``enable --now`` starts a unit only when it is not already running, so on a
    re-run that ships a changed unit file the old process would stay in place.
    ``try-restart`` picks the new definition up and, unlike ``restart``, leaves
    a deliberately stopped unit alone.
    """
    rc = run(["systemctl", "--user", "daemon-reload"])
    rc |= run(["systemctl", "--user", "enable", "--now", name])
    return rc | run(["systemctl", "--user", "try-restart", name])
