"""The two whole-disk surfaces, shared by the live ISO and the installed system.

`prepare_disk()` removes a disk's *identity* -- partition table and
filesystem signatures -- so it enters an install clean and no stale
UUID/PARTUUID can be resolved twice. `erase_disk()` destroys the
*contents*. They are one menu row each because they answer different
questions and deserve different confirmations: prepare asks yes/no,
erase makes you type the device path out.

**Why this lives in core/ and not installer/.** It started as an install
step, and the live ISO is still where most of it was measured -- but
nothing in either function is about installing. A second-hand USB stick
needs its identity wiped whether or not an Arch install is about to
follow, and the machine you would reach for to do it is the one already
running. `installer/erase.py` is the live-ISO caller and adds the two
things that only make sense there: the archiso gate, and forgetting a
partition selection that no longer exists. `core/tasks.py` is the
installed-system caller and adds `sudo`.

**The axis is capability, not class.** The obvious design is to detect
nvme/ssd/hdd and pick a command per kind. Measured 2026-08-30, that
axis does not survive: a SanDisk Cruzer Force USB stick reports
`rotational=1` like a platter drive does, and two NVMe drives on the
same bus disagree with each other about what erase operations they
offer (blockdev.py and nvme.py carry the tables). So the only question
asked here is whether the controller offers a firmware erase -- which
is asked of the controller -- and everything else overwrites.

**ATA Secure Erase is deliberately absent.** It would be the right
command for a directly-wired SATA SSD, and it is the one branch that
could not be measured: both SATA ports in reach carry spinning disks
holding real data, and the two USB-attached devices answer through a
bridge that does not pass ATA SECURITY. An interrupted secure erase
leaves the drive password-locked, so shipping it unmeasured would put
the expensive failure on the user. `erase_disk()` says this in words
rather than silently offering a lesser path.

**What prepare costs, measured.** `wipefs` and `blkdiscard` are both
util-linux, and util-linux is in `base` -- the same tautology the
ssd-trim decision records -- so this surface installs nothing. The
discard half is gated on `discard_max_bytes`, and the gate is not
cosmetic: measured 2026-08-30, both spinning disks report 0, and so
does a Kingston SATA SSD read through a USB enclosure although the
drive itself supports TRIM. A 0 means "no discard on this path", never
"this drive lacks it", and calling blkdiscard anyway would turn a
supported no-op into an error the user has to read past.

**What the installed system adds is one word, `sudo`, and one gate.**
The `sudo` flag follows `mirrors.rank()`, which had the same two callers
and the same problem before this did. The gate is `blockdev.refuse()`,
which grew a third reason for exactly this mode: on the live ISO the
disk that must never be touched is the medium it booted from, and on an
installed system it is the one the running system lives on. Neither
question answers the other -- `live_medium()` returns "" here.
"""

from __future__ import annotations

from typing import Callable

from . import blockdev, i18n, nvme
from .blockdev import Disk
from .pacman import run
from .prompt import ask_yes

t = i18n.t

# bs is large enough that syscall overhead disappears against the media and
# small enough to still show movement on a slow stick. conv=fsync so the exit
# code covers the writeback rather than just the queueing.
#
# count is not an optimisation, it is what keeps the exit code readable.
# Measured 2026-08-30 against /dev/full, which returns the same ENOSPC a full
# block device does: an unbounded `dd` reports `error writing` and exits **1**
# at exactly the moment it has succeeded, so a completed wipe would be filed
# as a failure. Sized to the byte it never reaches the end and rc stays 0.
DD_ARGS = ["bs=8M", "iflag=count_bytes", "status=progress", "conv=fsync"]


def _pick(prompt: str) -> Disk | None:
    """A whole disk, chosen by number, refused if it must not be touched."""
    disks = blockdev.list_disks()
    if not disks:
        print(t("blockdev.none"))
        return None

    print(f"\n{prompt}")
    for index, disk in enumerate(disks, 1):
        print(f"  {index:2}) {disk.label()}")
    raw = input(f"{t('inst.choice')}: ").strip()
    if not (raw.isdigit() and 1 <= int(raw) <= len(disks)):
        print(t("inst.invalid"))
        return None

    chosen = disks[int(raw) - 1]
    if (reason := blockdev.refuse(chosen.path)) is not None:
        print(reason)
        return None
    return chosen


def prepare_disk(
    sudo: bool = False, on_wiped: Callable[[str], None] | None = None
) -> int:
    """Wipe the partition table and filesystem signatures off one disk."""
    disk = _pick(t("blockdev.pick"))
    if disk is None:
        return 1
    prefix = ["sudo"] if sudo else []

    print(f"\n{t('prepare.warning', dev=disk.path)}")
    print(t("prepare.reversible"))
    if not ask_yes(t("prepare.confirm_q", dev=disk.path)):
        print(t("msg.cancelled"))
        return 0

    print(t("prepare.wiping"))
    # Partitions first, then the disk. Measured 2026-08-30 in QEMU: `wipefs
    # -a` on the whole disk removes only the signatures the *disk* carries
    # (GPT, PMBR). An ext4 superblock inside a partition sits at a
    # partition-relative offset that a whole-disk wipefs never looks at, so
    # it survives -- and the next partition table written at the same
    # alignment brings the old UUID straight back. Measured: prepare
    # reported "no partitions visible", one sgdisk later blkid answered
    # with the same UUID it had before, which is the exact thing this
    # surface exists to prevent. blkdiscard does not cover it either: the
    # discard was accepted (rc=0, discard_max_bytes non-zero) and changed
    # nothing, and on the paths this module already measured -- both
    # spinning disks, the USB-bridged SSD -- discard_max_bytes is 0, so
    # there is no discard to rely on in the first place.
    rc = 0
    for part in blockdev.partitions(disk.name):
        rc |= run([*prefix, "wipefs", "-a", part])
    rc |= run([*prefix, "wipefs", "-a", disk.path])
    if rc != 0:
        print(t("prepare.failed", dev=disk.path, rc=rc))
        return rc

    if disk.discard > 0:
        print(t("prepare.discard", size=disk.discard))
        rc |= run([*prefix, "blkdiscard", disk.path])
    else:
        print(t("prepare.no_discard"))

    if on_wiped is not None:
        on_wiped(disk.path)
    if rc != 0:
        print(t("prepare.failed", dev=disk.path, rc=rc))
        return rc
    print(t("prepare.done", dev=disk.path))
    return 0


def _nvme_plan(disk: Disk, sudo: bool = False) -> int | None:
    """The SES setting to format with, or None when the user backed out."""
    if not nvme.ensure_tool(sudo=sudo):
        return None
    available = nvme.modes(disk.path, sudo=sudo)
    print(f"\n{t('nvme.mode_q')}")
    for index, (_, label) in enumerate(available, 1):
        print(f"  {index}) {label}")
    raw = input(f"{t('inst.choice')} [1]: ").strip() or "1"
    if not (raw.isdigit() and 1 <= int(raw) <= len(available)):
        print(t("inst.invalid"))
        return None
    return available[int(raw) - 1][0]


def erase_disk(
    sudo: bool = False, on_wiped: Callable[[str], None] | None = None
) -> int:
    """Destroy the contents of one whole disk, irreversibly."""
    disk = _pick(t("blockdev.pick"))
    if disk is None:
        return 1
    prefix = ["sudo"] if sudo else []

    ses: int | None = None
    if disk.is_nvme:
        ses = _nvme_plan(disk, sudo=sudo)
        if ses is None:
            return 1
    else:
        if disk.size_bytes <= 0:
            # Overwriting needs an exact length; guessing one would either
            # stop short of the end or turn success into an error exit.
            print(t("erase.no_size", dev=disk.path))
            return 1
        # Not a refusal: the device is erasable, just not by its firmware.
        print(f"\n{t('erase.no_firmware', dev=disk.path)}")
        print(t("erase.overwrite_plan", size=disk.size))
        print(t("erase.overwrite_caveat"))

    print(f"\n{t('erase.warning', dev=disk.path, size=disk.size, model=disk.model or '?')}")
    if input(f"{t('nvme.confirm', dev=disk.path)}: ").strip() != disk.path:
        print(t("msg.cancelled"))
        return 1

    if ses is not None:
        rc = nvme.format_namespace(disk.path, ses, sudo=sudo)
    else:
        print(t("erase.overwriting", dev=disk.path))
        rc = run(
            [*prefix, "dd", "if=/dev/zero", f"of={disk.path}",
             f"count={disk.size_bytes}", *DD_ARGS]
        )

    if rc != 0:
        print(t("erase.failed", dev=disk.path, rc=rc))
        return rc
    if on_wiped is not None:
        on_wiped(disk.path)
    print(t("erase.done", dev=disk.path))
    return 0
