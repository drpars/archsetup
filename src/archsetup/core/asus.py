"""ASUS ROG/TUF tooling (asusctl, supergfxctl, rog-control-center).

If the [g14] repository is enabled in pacman.conf the ASUS packages come
from it; otherwise they are installed from the AUR. Services are only
enabled when their owning package actually got installed (the old script
enabled them unconditionally).
"""

from __future__ import annotations

import re
from pathlib import Path

from . import i18n, pacman, services

t = i18n.t

PACMAN_CONF = Path("/etc/pacman.conf")

G14_PACKAGES = ("asusctl", "supergfxctl", "rog-control-center")
REPO_PACKAGES = ("power-profiles-daemon", "switcheroo-control", "brightnessctl")

# service -> owning package
SERVICE_OWNERS = {
    "power-profiles-daemon": "power-profiles-daemon",
    "supergfxd": "supergfxctl",
    "switcheroo-control": "switcheroo-control",
}


def has_g14_repo() -> bool:
    try:
        text = PACMAN_CONF.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r"^\[g14\]", text, re.MULTILINE) is not None


def install() -> int:
    if has_g14_repo():
        print(t("asus.g14_found"))
        repo_pkgs = [*G14_PACKAGES, *REPO_PACKAGES]
        aur_pkgs: list[str] = []
    else:
        print(t("asus.g14_missing"))
        repo_pkgs = [*REPO_PACKAGES]
        aur_pkgs = [*G14_PACKAGES]

    rc = pacman.install(repo_pkgs, aur_pkgs)

    for service, package in SERVICE_OWNERS.items():
        if pacman.is_installed(package):
            rc |= services.enable(service)
    return rc
