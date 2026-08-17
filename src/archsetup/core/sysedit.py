"""Editing root-owned files: read as user, write back via sudo tee."""

from __future__ import annotations

import subprocess
import tempfile
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


def sudo_install(path: Path, content: str, owner: str, mode: str) -> int:
    """Place `content` at `path` owned by `owner`, not by root.

    sudo_write() leaves root:root behind, which is what /etc wants and what
    another account's home does not: the file is only useful there if that
    account can read it. install(1) sets owner and mode as part of the copy,
    so the file is never briefly owned by the wrong user, and a failed sudo
    leaves whatever was there untouched.

    The content goes through a temporary file because install(1) takes a
    source path, not stdin; `sudo tee` plus `sudo chown` would be two
    privileged calls and one window where the file has the wrong owner.
    """
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix="archsetup-"
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        return run(
            ["sudo", "install", "-o", owner, "-g", owner, "-m", mode,
             tmp.name, str(path)]
        )


def sudo_mkdir(path: Path, owner: str, mode: str) -> int:
    """Create one directory owned by `owner` (parents must already exist).

    Deliberately not `install -D` / `mkdir -p`: those create the missing
    parents with the default mode and root ownership, which for a chain
    inside someone else's home is the wrong answer for every link but the
    last. Callers walk the chain and name each link.
    """
    return run(
        ["sudo", "install", "-d", "-o", owner, "-g", owner, "-m", mode, str(path)]
    )


def write_with_backup(
    path: Path, content: str, backup: Path | None = None
) -> tuple[int, bool]:
    """Write `content` to `path`, backing up a differing existing file.

    Returns (exit code, changed). "changed" is what callers act on: the
    expensive follow-up of a config file -- mkinitcpio -P, udevadm reload --
    is worth running when the bytes moved and pure noise when they did not.

    `path` is expected to be a module constant, not a user-supplied name;
    with_suffix() would mangle anything containing a dot.

    `backup` moves the copy elsewhere, and exists for one specific hazard:
    writing into a drop-in directory whose *whole contents* are consumed.
    libvirt runs every executable file in hooks/<driver>.d/ "with any name",
    so the default sibling .bak -- copied with its mode, execute bit and all --
    becomes a second, older hook that runs on every VM start. Measured
    2026-08-04 with the vfio handover hook, where the old copy was the version
    that wedges the machine. Any caller writing into a directory something
    enumerates should point this somewhere that directory is not.
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
        backup = backup or path.with_suffix(path.suffix + ".bak")
        print(t("sysedit.backup", path=path, backup=backup))
        rc |= run(["sudo", "cp", str(path), str(backup)])

    rc |= sudo_write(path, content)
    return rc, True
