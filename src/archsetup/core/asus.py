"""ASUS ROG/TUF tooling (asusctl, rog-control-center).

Both packages are in [extra] now, so this task installs them like any other
repository package: no third-party stanza, no key to trust, no AUR fallback.
Measured 2026-09-06 on this laptop -- `pacman -Sl` carries extra/asusctl and
extra/rog-control-center at 6.4.0-2 with an archlinux.org packager, against
[ogc]'s 6.4.0.r0.ge6c1469c-1, which is the same upstream tag rebuilt.

What came out with that was not dead weight but a rule that had already
stopped firing here. man 5 pacman.conf: a name is served by the first
repository in the file "regardless of version number", and core.repos.insert
appends -- so in every file archsetup writes, [extra] is ahead of [ogc], and
asusctl started resolving from [extra] the moment it landed there. Nothing had
to be taken out of anyone's pacman.conf for that; measured the same day, this
machine has [extra] on line 84 and [ogc] on line 102.

The ordering rule itself is still real and still lives in core.repos. What
needs it now is linux-ogc: [ogc] is the only repository carrying that kernel,
and installer.base adds it when the kernel is chosen. This module is simply no
longer one of its callers.

A machine that installed these from [ogc] keeps that build. pacman.install
passes --needed, and by pacman's own comparison [ogc]'s version is the newer
of the two (`vercmp 6.4.0.r0.ge6c1469c-1 6.4.0-2` is 1), so nothing here
downgrades anything and no upgrade moves it either -- that resolves itself at
the next upstream release, when [extra]'s version wins on its own.

supergfxctl is gone from this module as of 2026-08-21, and neither it nor a
replacement is coming back. Its upstream is archived; measured 2026-08-13 it
is in neither [g14] nor [ogc], so it was an AUR build wherever it came from,
and it was the last AUR-only package any task here installed by itself.
Its successor cardwire is in [ogc] and does not stand in: measured
2026-08-15 out of the package contents, the eBPF program hooks
lsm/file_open, lsm/inode_getattr and lsm/inode_permission and all three deny
access to the device node, while nothing in the binary binds vfio-pci. It
hides the GPU instead of handing it over. The passthrough half has an owner
and it is not this tool -- see vfioctl.

cardwire is not in the catalog either, and the D-Bus name is a second reason
on top of that one. Its policy claims net.hadess.SwitcherooControl, which is
the name switcheroo-control owns -- the package REPO_PACKAGES installs and
SERVICE_OWNERS enables. Nothing in its .PKGINFO declares
replaces/conflicts/provides, so pacman would take both without a word, and
what happens when the two land together is unmeasured. A name this module
fills itself is not the place to ship an unmeasured collision.

NVIDIA laptop power management lives in core.nvidia_laptop.
"""

from __future__ import annotations

from . import i18n, pacman, services

t = i18n.t

# Two tuples although both halves come from the same place now: this is what
# the task is named for, and the other three are the freedesktop daemons it
# wants underneath -- SERVICE_OWNERS only ever speaks about the second half.
ASUS_PACKAGES = ("asusctl", "rog-control-center")
REPO_PACKAGES = ("power-profiles-daemon", "switcheroo-control", "brightnessctl")

# service -> owning package
SERVICE_OWNERS = {
    "power-profiles-daemon": "power-profiles-daemon",
    "switcheroo-control": "switcheroo-control",
}


def install() -> int:
    rc = pacman.install([*ASUS_PACKAGES, *REPO_PACKAGES], [])

    for service, package in SERVICE_OWNERS.items():
        if pacman.is_installed(package):
            rc |= services.enable(service)

    print(t("asus.nvidia_hint"))
    return rc
