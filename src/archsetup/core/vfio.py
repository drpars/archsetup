"""VFIO passthrough: a stable name for the GPU that stays home, the libvirt
hook that hands the other one over, and the one host-side holder that hook
cannot talk its way past.

Three tasks live here, and they are separate on purpose: the symlink is useful
on its own (it is what pins the compositor), while the hook is only safe once
nothing on the host still holds the discrete card.

**install_udev_rule** -- a stable name for the integrated GPU.

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

**install_handover_hook** -- the libvirt drop-in that moves the discrete card
to vfio-pci while a guest runs.

The behaviour is in data/vfio/50-vfio-handover and is not re-derived here; what
this task owns is *installing* it: where the file goes, which PCI addresses go
into vfio.conf, and the one precondition that decides whether installing makes
sense at all.

That precondition is the IOMMU group. The kernel hands a group to VFIO whole,
so a group holding anything besides the discrete GPU's own functions cannot be
passed through -- the leftover device would have to leave its host driver too.
Writing the config anyway would produce a hook that fails at VM start, on a
machine where nothing can make it succeed. Same shape as the symlink refusing a
non-hybrid machine: say no where the answer is structural.

**disable_xorg_autoaddgpu** -- stop the display manager's X greeter from
opening the discrete card and never letting go.

Pinning the compositor is not enough, because the compositor is not the only
thing running. SDDM starts a plain Xorg for its greeter and, with -noreset,
that server stays up for the whole login session -- measured here as the *only*
process holding the card, from boot onwards. Xorg does not need to display
anything on it: AutoAddGPU is on by default, so the card arrives from the udev
backend as a secondary GPU screen, nvidia-utils' 10-nvidia-drm-outputclass.conf
loads nvidia_drv.so for it, and that is enough to open the device. The primary
screen was the integrated GPU the whole time.

What that costs is the entire point of the project. Handing the card over with
Xorg still holding it makes the kernel say "Attempting to remove device ... with
non-zero usage count" and SIGABRT the greeter's server; SDDM loses its display
server and ends the user's session with it -- compositor, portals, terminals.
The handover would "succeed" at the price of everything it was supposed to
leave running. The hook cannot detect this either: its lsmod gate looks a
second later, by which time the kernel has already killed the holder and the
modules are gone.

Option "AutoAddGPU" "off" cuts exactly that link -- man xorg.conf: "no GPU
devices will be added from the udev backend" -- and leaves the primary screen
alone, since that one is not a GPU device and comes up the ordinary way. Three
other candidates were measured and dropped: DisplayServer=wayland needs weston,
which is not installed, and trades a measured certainty for an unknown; SDDM's
[X11] ServerArguments cannot scope it, because no such Xorg flag exists;
AutoBindGPU off only stops output-sink binding, the GPU screen and the open
device remain.

The file is global, though, which is the whole gate: on a machine that really
runs X11 desktops it would also take away their PRIME offload outputs. So the
task refuses where /usr/share/xsessions/ has anything in it, and this laptop is
the case where it does not exist at all -- the greeter is the only X server on
the machine.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .. import paths
from . import i18n, pacman, sysedit
from .pacman import run

t = i18n.t

DRM_CLASS = Path("/sys/class/drm")
UDEV_RULES = Path("/etc/udev/rules.d/70-vfio-igpu.rules")

PCI_DEVICES = Path("/sys/bus/pci/devices")
HOOK_NAME = "50-vfio-handover"
HOOK_DIR = Path("/etc/libvirt/hooks/qemu.d")
HOOK = HOOK_DIR / HOOK_NAME
# One level up, out of the drop-in directory: libvirt runs every executable
# file in qemu.d/ whatever it is called, so a sibling .bak is a second hook --
# and the copy being replaced is, by definition, the older behaviour. Nothing
# reads /etc/libvirt/hooks/ itself; libvirt wants the exact name "qemu" there.
HOOK_BACKUP = HOOK_DIR.parent / (HOOK_NAME + ".bak")
HOOK_ASSET = paths.DATA_DIR / "vfio" / HOOK_NAME
VFIO_CONF = Path("/etc/libvirt/hooks/vfio.conf")

XORG_CONF_DIR = Path("/etc/X11/xorg.conf.d")
XORG_AUTOADDGPU = XORG_CONF_DIR / "20-vfio-no-autoaddgpu.conf"
# X11 session desktop entries. Empty or absent means no X11 desktop is offered
# on this machine, which is what makes a global AutoAddGPU switch acceptable.
XSESSIONS = Path("/usr/share/xsessions")
# Where the running server says what it did. Only useful as a "before/after":
# it describes the Xorg that is up now, not the one the next boot will start.
XORG_LOG = Path("/var/log/Xorg.0.log")
# Xorg logs a GPU screen as "NVIDIA(G0)"; the primary screen would be
# "NVIDIA(0)". The G is the whole distinction being checked.
GPU_SCREEN_MARK = "NVIDIA(G"

AUTOADDGPU_CONF = """\
# Written by archsetup (task: vfio-xorg-autoaddgpu).
#
# Keeps the display manager's X greeter off the discrete GPU. Without this,
# Xorg adds the card from the udev backend as a secondary GPU screen, loads
# nvidia_drv.so for it and holds /dev/nvidia0 open for the whole session --
# even though it draws nothing there. Handing the card to a VM then makes the
# kernel kill the greeter's server, and SDDM ends the user's session with it.
#
# The primary screen is unaffected: it is not a GPU device and does not come
# from the udev backend. This file is global, so it is only installed on a
# machine that offers no X11 desktop session of its own -- on one that does, it
# would also remove its PRIME offload outputs.
Section "ServerFlags"
    Option "AutoAddGPU" "off"
EndSection
"""

# 0x03 covers VGA (0x0300), 3D (0x0302) and other display controllers. A
# laptop's discrete card can register as either depending on how the vBIOS
# and the driver came up, so matching the whole display class is the stable
# question; matching 0x0300 alone misses a card the host is not driving.
DISPLAY_CLASS_PREFIX = "0x03"

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


CONF_TEMPLATE = """\
# Written by archsetup (task: vfio-handover-hook); read by
# {hook}.
#
# IOMMU group {group}, which was checked to hold nothing but the discrete GPU's
# own functions -- the kernel hands a group to VFIO whole, so anything else in
# it would make passthrough impossible. The addresses are read off this machine
# rather than baked into the hook: they are the one value that differs per
# board.
VFIO_DEVICES="{devices}"
"""


def display_devices(vendor: str) -> list[str]:
    """PCI addresses of every display-class device belonging to `vendor`."""
    found = []
    try:
        entries = sorted(PCI_DEVICES.iterdir())
    except OSError:
        return found
    for device in entries:
        try:
            klass = (device / "class").read_text(encoding="utf-8").strip()
            device_vendor = (device / "vendor").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if device_vendor == vendor and klass.startswith(DISPLAY_CLASS_PREFIX):
            found.append(device.name)
    return found


def iommu_group(slot: str) -> tuple[str, list[str]] | None:
    """(group number, member addresses) for a device, or None if it has none.

    No group means the IOMMU is off -- on AMD it is on by default and needs no
    kernel flag, so this is a real "something is wrong" answer rather than a
    configuration step we could take on the user's behalf.
    """
    group = PCI_DEVICES / slot / "iommu_group"
    try:
        members = sorted(entry.name for entry in (group / "devices").iterdir())
    except OSError:
        return None
    return os.path.basename(os.path.realpath(group)), members


def _same_card(addr: str, other: str) -> bool:
    """Same PCI device, different function (01:00.0 vs 01:00.1)."""
    return addr.rsplit(".", 1)[0] == other.rsplit(".", 1)[0]


def conf_content(group: str, devices: list[str]) -> str:
    return CONF_TEMPLATE.format(hook=HOOK, group=group, devices=" ".join(devices))


def install_handover_hook() -> int:
    """Install the libvirt drop-in that moves the discrete GPU to vfio-pci."""
    if not pacman.is_installed("libvirt"):
        print(t("virt.libvirt_missing"))
        return 1

    dgpus = display_devices(NVIDIA_VENDOR)
    if not dgpus:
        print(t("vfio.no_dgpu"))
        return 1
    if len(dgpus) > 1:
        print(t("vfio.hook_many_dgpu", devices=" ".join(dgpus)))
        return 1
    slot = dgpus[0]

    group = iommu_group(slot)
    if group is None:
        print(t("vfio.hook_no_iommu", slot=slot))
        return 1
    number, members = group

    foreign = [member for member in members if not _same_card(member, slot)]
    if foreign:
        print(t("vfio.hook_group_dirty", group=number, devices=" ".join(foreign)))
        return 1
    print(t("vfio.hook_group_ok", group=number, devices=" ".join(members)))

    rc = run(["sudo", "mkdir", "-p", str(HOOK_DIR)])
    hook_rc, hook_changed = sysedit.write_with_backup(
        HOOK, HOOK_ASSET.read_text(encoding="utf-8"), backup=HOOK_BACKUP
    )
    rc |= hook_rc
    # libvirt silently skips a hook without the execute bit -- no error, no log
    # line, indistinguishable from not having installed it. Re-assert the mode
    # whenever it is missing, not only when the content changed.
    if hook_changed or not os.access(HOOK, os.X_OK):
        rc |= run(["sudo", "chmod", "0755", str(HOOK)])

    conf_rc, _ = sysedit.write_with_backup(VFIO_CONF, conf_content(number, members))
    rc |= conf_rc

    # The daemon reads its hook directory when it starts; a file dropped next to
    # a running libvirtd is ignored until then. try-restart rather than restart
    # because libvirtd is socket-activated here and usually is not running --
    # and vfio.conf needs no restart at all, the hook reads it on every call.
    if hook_changed:
        rc |= run(["sudo", "systemctl", "try-restart", "libvirtd.service"])

    print(t("vfio.hook_done", hook=HOOK, conf=VFIO_CONF))
    print(t("vfio.hook_verify", hook=HOOK))
    return rc


def x11_sessions() -> list[str]:
    """X11 desktop sessions this machine offers, by desktop-entry name."""
    try:
        return sorted(entry.name for entry in XSESSIONS.glob("*.desktop"))
    except OSError:
        return []


def gpu_screens() -> int | None:
    """NVIDIA GPU screens the *running* X server created; None if unreadable.

    This answers "is it in effect yet", not "is the file right": the log
    belongs to the server that is up now, which on a machine that has not
    rebooted was started before the file existed.
    """
    try:
        log = XORG_LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return log.count(GPU_SCREEN_MARK)


def disable_xorg_autoaddgpu() -> int:
    """Keep the display manager's X greeter from opening the discrete GPU."""
    detected = cards()
    nvidia = [card for card in detected if card[1] == NVIDIA_VENDOR]
    amd = [card for card in detected if card[1] == AMD_VENDOR]

    if not nvidia:
        print(t("vfio.no_dgpu"))
        return 1
    if len(amd) != 1:
        print(t("vfio.no_igpu", count=len(amd)))
        return 1

    # The one gate that matters, because the file is global rather than
    # scoped to the greeter -- no Xorg flag exists that could scope it.
    sessions = x11_sessions()
    if sessions:
        print(t("vfio.xorg_sessions", path=XSESSIONS, sessions=" ".join(sessions)))
        return 1

    rc = run(["sudo", "mkdir", "-p", str(XORG_CONF_DIR)])
    # Unlike libvirt's hook directory, a sibling .bak is harmless here: Xorg
    # reads only the files ending in ".conf" out of xorg.conf.d.
    write_rc, _ = sysedit.write_with_backup(XORG_AUTOADDGPU, AUTOADDGPU_CONF)
    rc |= write_rc

    screens = gpu_screens()
    if screens:
        print(t("vfio.xorg_pending", path=XORG_AUTOADDGPU, count=screens))
    elif screens == 0:
        print(t("vfio.xorg_effective", path=XORG_AUTOADDGPU))
    else:
        print(t("vfio.xorg_done", path=XORG_AUTOADDGPU))
    return rc
