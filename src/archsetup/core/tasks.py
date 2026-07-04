"""Named maintenance tasks.

Each task is a plain function returning an exit code, so it can run both
headlessly (`archsetup <task-id>`) and from the TUI (terminal suspended).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import gpuconfig, i18n, pacman
from .pacman import run

t = i18n.t

PACMAN_LOCK = Path("/var/lib/pacman/db.lck")


def system_update() -> int:
    return run(["sudo", "pacman", "-Syu"])


def clean_orphans() -> int:
    orphans = pacman.query(["pacman", "-Qqtd"])
    if not orphans:
        print(t("msg.no_orphans"))
        return 0
    return run(["sudo", "pacman", "-Rns", *orphans])


def clean_cache() -> int:
    return run(["sudo", "pacman", "-Sc"])


def update_keyring() -> int:
    return run(["sudo", "pacman", "-S", "--needed", "archlinux-keyring"])


def refresh_keys() -> int:
    return run(["sudo", "pacman-key", "--refresh-keys"])


def _edit(path: str) -> int:
    editor = os.environ.get("EDITOR") or (
        "nvim" if shutil.which("nvim") else "nano"
    )
    return run(["sudo", editor, path])


def edit_pacman_conf() -> int:
    return _edit("/etc/pacman.conf")


def edit_mirrorlist() -> int:
    return _edit("/etc/pacman.d/mirrorlist")


def _install_from_aur_git(url: str) -> int:
    with tempfile.TemporaryDirectory(prefix="archsetup-") as tmp:
        build_dir = f"{tmp}/build"
        rc = run(["git", "clone", url, build_dir])
        if rc != 0:
            return rc
        return run(["makepkg", "-si"], cwd=build_dir)


def install_yay() -> int:
    return _install_from_aur_git("https://aur.archlinux.org/yay-bin.git")


def install_paru() -> int:
    return _install_from_aur_git("https://aur.archlinux.org/paru-bin.git")


def remove_db_lock() -> int:
    if not PACMAN_LOCK.exists():
        print(t("msg.no_db_lock"))
        return 0
    return run(["sudo", "rm", str(PACMAN_LOCK)])


@dataclass(frozen=True)
class Task:
    id: str
    key: str  # locale key for the title; "<key>_desc" is the description
    fn: Callable[[], int]
    group: str = "update"  # which menu the task appears in


TASKS: tuple[Task, ...] = (
    Task("system-update", "task.system_update", system_update),
    Task("clean-orphans", "task.clean_orphans", clean_orphans),
    Task("clean-cache", "task.clean_cache", clean_cache),
    Task("update-keyring", "task.update_keyring", update_keyring),
    Task("refresh-keys", "task.refresh_keys", refresh_keys),
    Task("edit-pacman-conf", "task.edit_pacman_conf", edit_pacman_conf),
    Task("edit-mirrorlist", "task.edit_mirrorlist", edit_mirrorlist),
    Task("install-yay", "task.install_yay", install_yay),
    Task("install-paru", "task.install_paru", install_paru),
    Task("remove-db-lock", "task.remove_db_lock", remove_db_lock),
    Task(
        "nvidia-modules",
        "task.nvidia_modules",
        gpuconfig.configure_nvidia_modules,
        group="drivers",
    ),
    Task(
        "amd-modules",
        "task.amd_modules",
        gpuconfig.configure_amd_modules,
        group="drivers",
    ),
)


def get(task_id: str) -> Task | None:
    for task in TASKS:
        if task.id == task_id:
            return task
    return None
