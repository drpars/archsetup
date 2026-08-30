"""The two disk surfaces the live ISO offers before an install.

`prepare_disk()` removes a disk's *identity* -- partition table and
filesystem signatures -- so it enters an install clean and no stale
UUID/PARTUUID can be resolved twice. `erase_disk()` destroys the
*contents*. They are one menu row each because they answer different
questions and deserve different confirmations: prepare asks yes/no,
erase makes you type the device path out.

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
"""

from __future__ import annotations

from ..core import i18n
from ..core.pacman import run
from ..core.prompt import ask_yes
from . import blockdev, nvme
from .blockdev import Disk
from .disk import guard
from .state import state

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


def _forget_partitions(dev: str) -> None:
    """Drop selections pointing into a disk that no longer has them."""
    for attr in ("bootdev", "swapdev", "rootdev", "homedev"):
        if (value := getattr(state, attr)) and value.startswith(dev):
            setattr(state, attr, None)


def prepare_disk() -> int:
    """Wipe the partition table and filesystem signatures off one disk."""
    if not guard():
        return 1
    disk = _pick(t("blockdev.pick"))
    if disk is None:
        return 1

    print(f"\n{t('prepare.warning', dev=disk.path)}")
    print(t("prepare.reversible"))
    if not ask_yes(t("prepare.confirm_q", dev=disk.path)):
        print(t("msg.cancelled"))
        return 0

    print(t("prepare.wiping"))
    rc = run(["wipefs", "-a", disk.path])
    if rc != 0:
        print(t("prepare.failed", dev=disk.path, rc=rc))
        return rc

    if disk.discard > 0:
        print(t("prepare.discard", size=disk.discard))
        rc |= run(["blkdiscard", disk.path])
    else:
        print(t("prepare.no_discard"))

    _forget_partitions(disk.path)
    if rc != 0:
        print(t("prepare.failed", dev=disk.path, rc=rc))
        return rc
    print(t("prepare.done", dev=disk.path))
    return 0


def _nvme_plan(disk: Disk) -> int | None:
    """The SES setting to format with, or None when the user backed out."""
    if not nvme.ensure_tool():
        return None
    available = nvme.modes(disk.path)
    print(f"\n{t('nvme.mode_q')}")
    for index, (_, label) in enumerate(available, 1):
        print(f"  {index}) {label}")
    raw = input(f"{t('inst.choice')} [1]: ").strip() or "1"
    if not (raw.isdigit() and 1 <= int(raw) <= len(available)):
        print(t("inst.invalid"))
        return None
    return available[int(raw) - 1][0]


def erase_disk() -> int:
    """Destroy the contents of one whole disk, irreversibly."""
    if not guard():
        return 1
    disk = _pick(t("blockdev.pick"))
    if disk is None:
        return 1

    ses: int | None = None
    if disk.is_nvme:
        ses = _nvme_plan(disk)
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
        rc = nvme.format_namespace(disk.path, ses)
    else:
        print(t("erase.overwriting", dev=disk.path))
        rc = run(
            ["dd", "if=/dev/zero", f"of={disk.path}", f"count={disk.size_bytes}", *DD_ARGS]
        )

    if rc != 0:
        print(t("erase.failed", dev=disk.path, rc=rc))
        return rc
    _forget_partitions(disk.path)
    print(t("erase.done", dev=disk.path))
    return 0
