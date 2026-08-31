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

Measured 2026-08-21 against the config of every kernel this installer
offers, the DKMS path is reached on exactly one of the five:

    linux, linux-lts, linux-zen   CONFIG_ANDROID_BINDER_IPC_RUST=y  built in
    linux-ogc                     CONFIG_ANDROID_BINDER_IPC=y +
                                  CONFIG_ANDROID_BINDERFS=y         built in
    linux-hardened                neither, and CONFIG_RUST is not
                                  set at all, so the Rust driver is
                                  not even offered                  DKMS

That makes the DKMS branch rare rather than dead, and rare is what makes
the headers check worth having (core/dkms.py, shared with the ddcci task):
it is the branch nobody exercises, so its silent failure is the one nobody
would notice.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import dkms, i18n, pacman, services
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

    # python-pyclip goes in the repository list: it is extra/python-pyclip
    # 0.7.0, and its AUR record no longer exists at all -- a promoted package
    # gets its AUR entry deleted as a duplicate (measured 2026-08-21, the RPC
    # returns nothing for the name). It worked anyway because AUR helpers
    # resolve repository names too, which is precisely why nothing reported
    # it. The two lists are kept apart so a dead name in one cannot take the
    # other down, and a repository package riding in the AUR transaction
    # gives that up for free.
    repo_packages = ["python-pyclip"]
    release = os.uname().release
    if not dkms.headers_present(release):
        headers = dkms.headers_package(release)
        # Refusing beats proceeding: the known outcome of a DKMS install with
        # no headers is a success message and no module, and that is harder
        # to diagnose than a task that stops and says what it needs.
        if headers is None:
            print(t("dkms.headers_unknown", release=release))
            return 1
        print(t("dkms.headers_needed", package=headers))
        repo_packages.append(headers)

    rc = pacman.install(repo_packages, ["binder_linux-dkms"])
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
