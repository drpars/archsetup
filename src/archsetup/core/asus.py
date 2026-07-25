"""ASUS ROG/TUF tooling (asusctl, rog-control-center).

Follows https://asus-linux.org/guides/arch-guide/. The [g14] repository is
set up on demand (key import + pacman.conf stanza) so the ASUS packages come
from upstream rather than the AUR; if the user declines we fall back to AUR
builds. Services are only enabled when their owning package actually got
installed (the old script enabled them unconditionally).

supergfxctl is deliberately not part of the default set: upstream is phasing
it out and recommends it only for VM passthrough or when the dGPU cannot be
powered down. It stays available as its own task. NVIDIA laptop power
management lives in core.nvidia_laptop.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import i18n, pacman, prompt, services
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

PACMAN_CONF = Path("/etc/pacman.conf")

G14_KEY = "8F654886F17D497FEFE3DB448B15A6B0E9A3FA35"
G14_STANZA = "\n[g14]\nServer = https://arch.asus-linux.org\n"

G14_PACKAGES = ("asusctl", "rog-control-center")
REPO_PACKAGES = ("power-profiles-daemon", "switcheroo-control", "brightnessctl")
SUPERGFX_PACKAGES = ("supergfxctl",)

# service -> owning package
SERVICE_OWNERS = {
    "power-profiles-daemon": "power-profiles-daemon",
    "switcheroo-control": "switcheroo-control",
}


def has_g14_repo() -> bool:
    try:
        text = PACMAN_CONF.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r"^\[g14\]", text, re.MULTILINE) is not None


def setup_g14_repo() -> bool:
    """Import the signing key and append the [g14] stanza. True on success."""
    rc = run(["sudo", "pacman-key", "--recv-keys", G14_KEY])
    rc |= run(["sudo", "pacman-key", "--lsign-key", G14_KEY])
    if rc != 0:
        print(t("asus.g14_key_failed"))
        return False

    try:
        text = PACMAN_CONF.read_text(encoding="utf-8")
    except OSError:
        print(t("asus.pacman_conf_unreadable", path=PACMAN_CONF))
        return False

    # Appended, so the official repositories keep priority over [g14].
    if sudo_write(PACMAN_CONF, text.rstrip("\n") + "\n" + G14_STANZA) != 0:
        return False
    return run(["sudo", "pacman", "-Suy"]) == 0


def install() -> int:
    if has_g14_repo():
        print(t("asus.g14_found"))
        use_repo = True
    elif prompt.ask_yes(t("asus.g14_setup_q")):
        use_repo = setup_g14_repo()
    else:
        use_repo = False

    if use_repo:
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

    print(t("asus.nvidia_hint"))
    return rc


def install_supergfx() -> int:
    """Optional: only needed for VM passthrough or forcing the dGPU off."""
    print(t("asus.supergfx_note"))
    if has_g14_repo():
        rc = pacman.install([*SUPERGFX_PACKAGES], [])
    else:
        rc = pacman.install([], [*SUPERGFX_PACKAGES])
    if pacman.is_installed("supergfxctl"):
        rc |= services.enable("supergfxd")
    return rc
