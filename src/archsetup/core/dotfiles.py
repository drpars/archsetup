"""Dotfiles management: clone/pull, copy with rsync backups, symlink, validate.

The repo lives directly in ~/.dotfiles with config/ and home/ sections.
Copy mode mirrors items with rsync (previous versions saved under
~/Documents/dotfiles_yedek/<timestamp>); symlink mode backs up existing
targets and creates links atomically (temp link + rename).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import i18n
from .pacman import run
from .prompt import ask_yes

t = i18n.t

REPO_BASE = "https://github.com/drpars"
DOTFILES_DIR = Path.home() / ".dotfiles"


def section_target(section: str) -> Path:
    return {"config": Path.home() / ".config", "home": Path.home()}[section]


def ensure_repo(name: str, target: Path) -> int:
    if (target / ".git").is_dir():
        return run(["git", "-C", str(target), "pull", "--ff-only"])
    target.parent.mkdir(parents=True, exist_ok=True)
    return run(
        ["git", "clone", "--depth", "1", f"{REPO_BASE}/{name}.git", str(target)]
    )


def ensure_dotfiles_repo() -> int:
    return ensure_repo("dotfiles", DOTFILES_DIR)


def list_items(section: str) -> list[str]:
    base = DOTFILES_DIR / section
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir())


def _backup_dir() -> Path:
    out = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True)
    docs = Path(out.stdout.strip()) if out.stdout.strip() else Path.home() / "Documents"
    backup = docs / "dotfiles_yedek" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True, exist_ok=True)
    return backup


def _rsync_cmd(source: Path, target: Path, backup: Path, dry: bool) -> list[str]:
    src = f"{source}/" if source.is_dir() else str(source)
    cmd = ["rsync", "-avh", "--backup", f"--backup-dir={backup}", "--delete"]
    if dry:
        cmd.append("--dry-run")
    return [*cmd, src, str(target)]


def copy_items(section: str, items: list[str]) -> int:
    if shutil.which("rsync") is None:
        if run(["sudo", "pacman", "-S", "--needed", "rsync"]) != 0:
            return 1

    target_base = section_target(section)
    backup = _backup_dir()

    for item in items:
        print(f"─── {item} ───")
        run(_rsync_cmd(DOTFILES_DIR / section / item, target_base / item, backup, True))
    if not ask_yes(t("dotfiles.apply_q", backup=backup)):
        print(t("msg.cancelled"))
        return 0

    rc = 0
    for item in items:
        target = target_base / item
        if target.is_symlink():
            print(t("dotfiles.removing_link", target=target))
            target.unlink()
        rc |= run(_rsync_cmd(DOTFILES_DIR / section / item, target, backup, False))
    return rc


def symlink_items(section: str, items: list[str]) -> int:
    target_base = section_target(section)
    backup = _backup_dir()

    for item in items:
        source = DOTFILES_DIR / section / item
        target = target_base / item
        if target.exists() or target.is_symlink():
            print(t("dotfiles.backing_up", target=target))
            shutil.move(str(target), str(backup / item))
        target.parent.mkdir(parents=True, exist_ok=True)

        temp = target.parent / f"{target.name}.newlink"
        if temp.exists() or temp.is_symlink():
            temp.unlink()
        os.symlink(source, temp)
        os.replace(temp, target)
        print(f"{target} → {source}")
    return validate_items(section, items)


def validate_items(section: str, items: list[str]) -> int:
    target_base = section_target(section)
    broken = []
    rc = 0

    for item in items:
        target = target_base / item
        if not target.is_symlink():
            print(t("dotfiles.not_symlink", target=target))
            rc = 1
        elif not Path(os.path.realpath(target)).exists():
            broken.append(f"{target} → {os.readlink(target)}")
            rc = 1

    if broken:
        print(t("dotfiles.broken_links"))
        for link in broken:
            print(f"  - {link}")
    elif rc == 0:
        print(t("dotfiles.links_ok"))
    return rc


def install_nvim() -> int:
    nvim_config = Path.home() / ".config" / "nvim"
    rc = ensure_repo("nvim", nvim_config)
    if rc != 0:
        return rc
    if ask_yes(t("dotfiles.nvim_root_q")):
        rc |= run(["sudo", "mkdir", "-p", "/root/.config"])
        rc |= run(["sudo", "ln", "-sfn", str(nvim_config), "/root/.config/nvim"])
    return rc


def remove_nvim() -> int:
    if not ask_yes(t("dotfiles.nvim_remove_q")):
        print(t("msg.cancelled"))
        return 0
    for directory in (
        Path.home() / ".config" / "nvim",
        Path.home() / ".local" / "share" / "nvim",
        Path.home() / ".cache" / "nvim",
    ):
        shutil.rmtree(directory, ignore_errors=True)
        print(t("dotfiles.removed", path=directory))
    return 0
