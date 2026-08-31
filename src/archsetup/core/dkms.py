"""What a DKMS package needs before it can build: a tree to build against.

Extracted from waydroid.py, which measured the failure this guards: a DKMS
package installs perfectly happily with no kernel headers. The build fails,
pacman still reports success, and the symptom arrives much later as a device
that never appeared. Two tasks now install DKMS packages, so the check has one
owner rather than a copy in each -- a second copy would be the one that stops
getting fixed.

Both answers come from the running kernel rather than from a table of package
names. That is not tidiness: the table would have known linux-zen and not
linux-ogc, and the branch is rare enough that nobody would have noticed.
"""

from __future__ import annotations

from pathlib import Path

KERNEL_MODULES = Path("/usr/lib/modules")


def headers_present(release: str) -> bool:
    """Whether DKMS has a tree to build against, asked directly.

    /usr/lib/modules/<release>/build is what the build reads, and it is there
    exactly when the headers package is installed -- whatever that package is
    called. Asking for the thing itself beats asking whether some package
    name is installed.
    """
    return (KERNEL_MODULES / release / "build").exists()


def headers_package(release: str) -> str | None:
    """The headers package for the running kernel, from the kernel, not a table.

    The kernel writes its own pkgbase next to its modules (measured: linux-zen
    on both machines this was tested on), so the name is derived from it.
    None means the kernel did not say, and a caller that cannot name the
    package should stop rather than guess one.
    """
    try:
        base = (KERNEL_MODULES / release / "pkgbase").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return None
    return f"{base}-headers" if base else None


def module_built(release: str, name: str) -> bool:
    """Whether DKMS actually left a built module behind for this kernel.

    The directory is not the answer, and reading it as one is the mistake this
    function exists to stop: updates/dkms holds every DKMS module on the
    machine, so it is there the moment any one of them builds. Measured
    2026-08-31 on a machine with four of them -- acpi_call, nvidia,
    openrazer-driver and ddcci -- all in that one directory.

    The module file is the answer. Its compression suffix is a kernel build
    option (measured: ddcci.ko.zst on linux-zen), so the glob stops at .ko.
    """
    directory = KERNEL_MODULES / release / "updates" / "dkms"
    try:
        return any(directory.glob(f"{name}.ko*"))
    except OSError:
        return False
