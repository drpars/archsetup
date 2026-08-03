"""VFIO passthrough groundwork: a stable name for the GPU that stays home.

Handing the discrete GPU to a VM means telling the compositor, before the
handover, to drive the integrated GPU and nothing else. Hyprland reads that
from AQ_DRM_DEVICES -- and that variable is where every obvious way of naming
a card falls over:

- ``/dev/dri/card*`` numbers are not stable. On this laptop the dGPU came up
  as card1 on one boot and card0 on the next, so a config naming a number
  points at the wrong GPU sooner or later.
- ``/dev/dri/by-path/pci-0000:05:00.0-card`` is stable but unusable here:
  AQ_DRM_DEVICES splits its list on ":", so a PCI address arrives as several
  broken paths. This was diagnosed the hard way, and it is documented upstream
  at https://wiki.hypr.land/Configuring/Advanced-and-Cool/Multi-GPU/

What is left is a name of our own: a udev SYMLINK carrying neither a colon nor
a card number. The PCI address is read off this machine rather than baked in,
because it is the one value that legitimately differs per board.

This task writes only the rule. The compositor half -- AQ_DRM_DEVICES plus the
EGL/GLX/Vulkan ICD variables that must accompany it -- belongs to the user's
session and lives in dotfiles; the rule alone changes nothing, which is why
the task says so when it finishes.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from . import i18n, sysedit
from .pacman import run

t = i18n.t

DRM_CLASS = Path("/sys/class/drm")
UDEV_RULES = Path("/etc/udev/rules.d/70-vfio-igpu.rules")

# The name the compositor will be pointed at. Kept free of ":" on purpose.
SYMLINK = "dri/amd-igpu"
DEV_LINK = Path("/dev") / SYMLINK

AMD_VENDOR = "0x1002"
NVIDIA_VENDOR = "0x10de"

# /sys/class/drm holds cards *and* connectors (card1-DP-1, card1-eDP-1...).
# Only the bare cardN entries have a device node to symlink.
_CARD_NAME = re.compile(r"^card\d+$")

RULE_TEMPLATE = """\
# Written by archsetup. Stable name for the GPU that keeps driving the host
# while the discrete card is bound to vfio-pci. Card numbers change between
# boots and AQ_DRM_DEVICES splits on ":", so by-path names cannot be used.
KERNEL=="card*", KERNELS=="{slot}", SUBSYSTEM=="drm", SUBSYSTEMS=="pci", SYMLINK+="{symlink}"
"""


def cards() -> list[tuple[str, str, str]]:
    """(card name, PCI vendor id, PCI slot) for every DRM card on the machine."""
    found = []
    for card in sorted(DRM_CLASS.glob("card*")):
        if not _CARD_NAME.match(card.name):
            continue
        device = card / "device"
        try:
            vendor = (device / "vendor").read_text(encoding="utf-8").strip()
            uevent = (device / "uevent").read_text(encoding="utf-8")
        except OSError:
            continue
        for line in uevent.splitlines():
            key, _, value = line.partition("=")
            if key == "PCI_SLOT_NAME" and value.strip():
                found.append((card.name, vendor, value.strip()))
                break
    return found


def rule_content(slot: str) -> str:
    return RULE_TEMPLATE.format(slot=slot, symlink=SYMLINK)


def install_udev_rule() -> int:
    """Give the integrated GPU a name the compositor can be pinned to.

    Refuses on a machine that is not hybrid instead of guessing. A desktop with
    a single NVIDIA card has no GPU to fall back to, so a symlink there would
    name the very card we are about to take away -- silently breaking the
    session rather than protecting it.
    """
    detected = cards()
    amd = [card for card in detected if card[1] == AMD_VENDOR]
    nvidia = [card for card in detected if card[1] == NVIDIA_VENDOR]

    if not nvidia:
        print(t("vfio.no_dgpu"))
        return 1
    if len(amd) != 1:
        print(t("vfio.no_igpu", count=len(amd)))
        return 1

    name, _, slot = amd[0]
    print(t("vfio.found", card=name, slot=slot))

    rc, changed = sysedit.write_with_backup(UDEV_RULES, rule_content(slot))
    if changed:
        rc |= run(["sudo", "udevadm", "control", "--reload-rules"])
        # --settle so the check below reads the result, not the race.
        rc |= run(["sudo", "udevadm", "trigger", "--settle", "--subsystem-match=drm"])

    if DEV_LINK.is_symlink():
        print(t("vfio.link_ok", link=DEV_LINK, target=os.path.realpath(DEV_LINK)))
    else:
        # The rule is on disk and correct for the next boot, but something
        # stopped udev from acting on it now; saying "done" here would be a lie.
        print(t("vfio.link_missing", link=DEV_LINK))
        rc |= 1

    print(t("vfio.next_step", link=DEV_LINK))
    return rc
