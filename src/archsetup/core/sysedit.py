"""Editing root-owned files: read as user, write back via sudo tee."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import i18n
from .pacman import run

t = i18n.t


def sudo_write(path: Path, content: str) -> int:
    print(f"\033[1;36m$ sudo tee {path}\033[0m")
    proc = subprocess.run(
        ["sudo", "tee", str(path)],
        input=content,
        text=True,
        stdout=subprocess.DEVNULL,
    )
    return proc.returncode


def write_with_backup(path: Path, content: str) -> tuple[int, bool]:
    """Write `content` to `path`, backing up a differing existing file.

    Returns (exit code, changed). "changed" is what callers act on: the
    expensive follow-up of a config file -- mkinitcpio -P, udevadm reload --
    is worth running when the bytes moved and pure noise when they did not.

    `path` is expected to be a module constant, not a user-supplied name;
    with_suffix() would mangle anything containing a dot.
    """
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = None

    if current == content:
        print(t("sysedit.unchanged", path=path))
        return 0, False

    rc = 0
    if current is not None:
        backup = path.with_suffix(path.suffix + ".bak")
        print(t("sysedit.backup", path=path, backup=backup))
        rc |= run(["sudo", "cp", str(path), str(backup)])

    rc |= sudo_write(path, content)
    return rc, True
