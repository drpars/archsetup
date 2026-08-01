"""pacman / AUR helper wrapper. All commands run in the real terminal
(headless mode, or while the TUI is suspended)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile

from . import i18n

t = i18n.t

AUR_HELPERS = ("yay", "paru")
YAY_BIN = "https://aur.archlinux.org/yay-bin.git"


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


def install_from_aur_git(url: str) -> int:
    """Clone an AUR package and makepkg -si it, with no helper involved."""
    with tempfile.TemporaryDirectory(prefix="archsetup-") as tmp:
        build_dir = f"{tmp}/build"
        rc = run(["git", "clone", url, build_dir])
        if rc != 0:
            return rc
        return run(["makepkg", "-si"], cwd=build_dir)


def ensure_aur_helper() -> str | None:
    """Return an AUR helper, offering to bootstrap one if there is none.

    On a fresh system the first task someone runs may well need an AUR
    package, and yay itself comes from the AUR -- so pointing at the
    System Update menu and failing just moved the problem. yay-bin is
    prebuilt, so this costs a clone and a package install rather than a
    Go toolchain and a compile.
    """
    helper = detect_aur_helper()
    if helper is not None:
        return helper

    from .prompt import ask_yes

    print(t("msg.aur_missing"))
    if not ask_yes(t("msg.aur_install_q")):
        return None
    if install_from_aur_git(YAY_BIN) != 0:
        return None
    return detect_aur_helper()


def install(repo_pkgs: list[str], aur_pkgs: list[str]) -> int:
    """Repo packages and AUR packages in two separate transactions.

    The AUR helper is never given --noconfirm, and that is a rule rather
    than an oversight: its PKGBUILD diff prompt is the only thing standing
    between a poisoned package and this machine. The 2026 "Atomic Arch"
    campaign took over orphaned AUR packages and injected an infostealer
    into the PKGBUILD; reading the diff before building was what caught
    it. Adding the flag to make installs flow better would silently
    switch that off, so test_aur_helper_is_never_silenced watches for it.
    """
    rc = 0
    if repo_pkgs:
        rc |= run(["sudo", "pacman", "-S", "--needed", *repo_pkgs])
    if aur_pkgs:
        helper = ensure_aur_helper()
        if helper is None:
            rc |= 1
        else:
            rc |= run([helper, "-S", "--needed", *aur_pkgs])
    return rc
