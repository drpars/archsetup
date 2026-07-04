"""pacman / AUR helper wrapper. All commands run in the real terminal
(headless mode, or while the TUI is suspended)."""

from __future__ import annotations

import shutil
import subprocess

from . import i18n

t = i18n.t

AUR_HELPERS = ("yay", "paru")


def detect_aur_helper() -> str | None:
    for helper in AUR_HELPERS:
        if shutil.which(helper):
            return helper
    return None


def run(cmd: list[str], **kwargs) -> int:
    print(f"\033[1;36m$ {' '.join(cmd)}\033[0m")
    return subprocess.call(cmd, **kwargs)


def query(cmd: list[str]) -> list[str]:
    """Run a query command and return stdout lines (empty on failure)."""
    out = subprocess.run(cmd, capture_output=True, text=True)
    return out.stdout.split()


def is_installed(pkg: str) -> bool:
    return subprocess.run(
        ["pacman", "-Qq", pkg], capture_output=True
    ).returncode == 0


def install(repo_pkgs: list[str], aur_pkgs: list[str]) -> int:
    rc = 0
    if repo_pkgs:
        rc |= run(["sudo", "pacman", "-S", "--needed", *repo_pkgs])
    if aur_pkgs:
        helper = detect_aur_helper()
        if helper is None:
            print(f"\033[1;31m{t('msg.aur_missing')}\033[0m")
            rc |= 1
        else:
            rc |= run([helper, "-S", "--needed", *aur_pkgs])
    return rc
