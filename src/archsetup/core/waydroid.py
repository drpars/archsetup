"""Waydroid binder module setup for kernels without built-in binder.

The old script appended a bare "device=binder,hwbinder,vndbinder" to the
kernel cmdline — unprefixed module parameters there do nothing (same bug
class as the bare modeset=1). The documented mechanism is modprobe.d
options for binder_linux plus modules-load.d, which also works with any
bootloader.

Kernels that ship binder built in need none of that, and installing the DKMS
module on top of one actively breaks the boot: it registers the same name the
built-in driver already holds, fails with EBUSY and takes
systemd-modules-load.service down with it. The kernel is asked directly rather
than matched against a list of kernel package names — linux-zen was the only
one the list knew, while linux-g14 gained a built-in binder later
(CONFIG_ANDROID_BINDER_IPC_RUST=y) and kept hitting the DKMS path.
"""

from __future__ import annotations

from pathlib import Path

from . import i18n, pacman, services
from .sysedit import sudo_write

t = i18n.t

MODULES_LOAD = Path("/etc/modules-load.d/binder_linux.conf")
MODPROBE = Path("/etc/modprobe.d/binder_linux.conf")
FILESYSTEMS = Path("/proc/filesystems")


def has_builtin_binder() -> bool:
    """True when the running kernel already provides the binder filesystem.

    /proc/filesystems lists it as "nodev\tbinder"; the second field is the
    name, so a substring match would also fire on binderfs.
    """
    try:
        text = FILESYSTEMS.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.split()[-1:] == ["binder"] for line in text.splitlines())


def setup() -> int:
    if not pacman.is_installed("waydroid"):
        print(t("waydroid.missing"))
        return 1

    if has_builtin_binder():
        print(t("waydroid.builtin"))
        return services.enable("waydroid-container")

    rc = pacman.install([], ["binder_linux-dkms", "python-pyclip"])
    if rc != 0:
        return rc

    rc = sudo_write(MODULES_LOAD, "binder_linux\n")
    rc |= sudo_write(
        MODPROBE, "options binder_linux devices=binder,hwbinder,vndbinder\n"
    )
    rc |= services.enable("waydroid-container")
    if rc == 0:
        print(t("waydroid.done"))
    return rc
