"""Waydroid binder module setup for kernels without built-in binder.

The old script appended a bare "device=binder,hwbinder,vndbinder" to the
kernel cmdline — unprefixed module parameters there do nothing (same bug
class as the bare modeset=1). The documented mechanism is modprobe.d
options for binder_linux plus modules-load.d, which also works with any
bootloader; linux-zen ships binder built in, so only the service is
enabled there.
"""

from __future__ import annotations

from pathlib import Path

from . import i18n, pacman, services
from .sysedit import sudo_write

t = i18n.t

MODULES_LOAD = Path("/etc/modules-load.d/binder_linux.conf")
MODPROBE = Path("/etc/modprobe.d/binder_linux.conf")


def setup() -> int:
    if not pacman.is_installed("waydroid"):
        print(t("waydroid.missing"))
        return 1

    if pacman.is_installed("linux-zen"):
        print(t("waydroid.zen"))
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
