"""Whole-disk inventory and the gates every destructive disk step shares.

Why this module exists as a layer of its own: the erase surfaces need to
answer three questions before they touch anything, and each one had a
wrong-looking cheap answer that measurement rejected.

**Which disks are there.** `nvme list` only ever sees NVMe, so the older
reset surface could not offer a SATA or USB device at all. `lsblk` with
TYPE=disk sees all of them and names the transport in the same row.

**What class is it.** Measured 2026-08-30 on two machines, four whole
disks each:

    PANTHERA-DR    sda  Cruzer Force (USB flash)  rota=1 rm=1 disc=0 tran=usb
                   sdb  KINGSTON SHFS37A240G      rota=0 rm=0 disc=0 tran=usb
                   nvme0n1 KIOXIA EXCERIA PLUS G4 rota=0 rm=0 disc=2T tran=nvme
                   nvme1n1 Crucial P3 Plus        rota=0 rm=0 disc=2T tran=nvme
    PANTHERA-ARCH  sda  ST4000DM006 (3.5" HDD)    rota=1 rm=0 disc=0 tran=sata
                   sdb  TOSHIBA MQ01ABB200 (2.5") rota=1 rm=0 disc=0 tran=sata

`queue/rotational` is **not** read anywhere here, and the first row is
why: a flash stick reports 1. The attribute says "the device did not
claim to be non-rotational", which is a different sentence from "this
platter spins", and a classifier built on it calls a USB stick a hard
disk. `removable` splits the two USB devices from each other (1 and 0)
without splitting either from the HDDs, so it does not answer the
question either -- the same measurement writeback.py already recorded
for its own rule. `ID_BUS` reads `ata` through that enclosure while
lsblk's TRAN reads `usb` for the same device: two sources, one of them
wrong for transport, and TRAN is the one that is right.

So nothing here branches on class. The only branch that survived
measurement is a **capability** one, and capability is asked of the
device rather than derived from its kind -- see `erase.py`.

**Is it safe to touch.** `busy()` used to live in `nvme.py`, which meant
`disk.format_devices()` had no in-use check at all: mkfs on a mounted
device was one `ask_yes` away. The prefix match is deliberate, and the
reason is unchanged from where it was written: `/dev/nvme0n1p2` being
mounted puts the whole namespace off limits, not just that partition.

`live_medium()` closes the other half. `disk.guard()` asks "am I inside
the live ISO", which is not the same question as "is this the stick I
booted from" -- and on this laptop the boot stick and the expendable
test stick are both `sd*` on the same bus.

Not measured: whether any USB bridge here passes ATA SECURITY commands
(no tool installed, and no expendable internal SATA device exists on
either machine), and what `discard_max_bytes` reads for a SATA SSD wired
directly -- both machines' SATA ports carry spinning disks. The Kingston
SSD reports 0 through its enclosure although the drive supports TRIM, so
a 0 here means "no discard on this path", never "the drive lacks it".
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core import i18n
from ..core.writeback import backing_disk

t = i18n.t

BLOCK = Path("/sys/block")

# (file, label) -- label None means "report the second column", the mountpoint.
IN_USE_SOURCES = (("/proc/mounts", None), ("/proc/swaps", "swap"))

# Where archiso mounts the medium it booted from. Refusing this is not the
# same guard as disk.guard(): that one asks whether we are in the live ISO.
ARCHISO_MOUNT = "/run/archiso/bootmnt"


@dataclass(frozen=True)
class Disk:
    """One whole disk, as lsblk names it."""

    path: str  # /dev/sda
    size: str  # 58,7G
    tran: str  # usb | nvme | sata | ""
    model: str
    discard: int  # queue/discard_max_bytes, 0 when the path offers none
    size_bytes: int  # exact capacity; 0 when sysfs could not answer

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def is_nvme(self) -> bool:
        """Transport, not the kernel name: a name test would also match
        a partition, and the erase backend needs the controller."""
        return self.tran == "nvme"

    def label(self) -> str:
        parts = [f"{self.path}", f"{self.size:>8}"]
        if self.tran:
            parts.append(f"{self.tran:>4}")
        parts.append(self.model or "?")
        return "  ".join(parts)


def discard_bytes(name: str) -> int:
    try:
        return int((BLOCK / name / "queue/discard_max_bytes").read_text())
    except (OSError, ValueError):
        return 0


def size_bytes(name: str) -> int:
    """Exact capacity in bytes, or 0 when sysfs could not answer.

    From sysfs rather than `blockdev --getsize64` because this is read
    while drawing a list: sysfs needs no privilege (measured -- blockdev
    returns EACCES for a non-root caller), and `size` is in 512-byte
    units regardless of the device's own logical block size. Cross-checked
    2026-08-30: nvme0n1 gives 1,000,204,886,016, the same figure a
    byte-for-byte read of that disk produced in a separate project.
    """
    try:
        return int((BLOCK / name / "size").read_text()) * 512
    except (OSError, ValueError):
        return 0


def list_disks() -> list[Disk]:
    """Whole disks only -- partitions are never an erase target.

    JSON rather than columns, for the same reason nvme.py parses `nvme
    list -o json`: measured 2026-08-30, a device with no transport (zram
    reports TYPE=disk and an empty TRAN) shifts MODEL into the TRAN
    column under `-n`, and `-r` escapes the spaces inside model names to
    `\\x20`. JSON gives null and keeps the string intact.

    `-e 7,11` drops loop and sr the way disk.list_devices() does; the
    `device/` link is not consulted here because TYPE=disk already
    excludes the dm/md nodes that made trim.py need it.
    """
    try:
        out = subprocess.run(
            ["lsblk", "-p", "-d", "-o", "NAME,SIZE,TYPE,TRAN,MODEL", "-e", "7,11", "--json"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if out.returncode != 0:
        return []
    try:
        entries = json.loads(out.stdout).get("blockdevices", [])
    except json.JSONDecodeError:
        return []

    disks = []
    for entry in entries:
        if entry.get("type") != "disk" or not entry.get("name"):
            continue
        path = entry["name"]
        name = Path(path).name
        disks.append(
            Disk(
                path=path,
                size=entry.get("size") or "?",
                tran=entry.get("tran") or "",
                model=(entry.get("model") or "").strip(),
                discard=discard_bytes(name),
                size_bytes=size_bytes(name),
            )
        )
    return disks


def busy(dev: str) -> str | None:
    """Mountpoint or 'swap' if `dev` -- or any partition of it -- is in use.

    The prefix match is deliberate: /dev/nvme0n1p2 being mounted means the
    whole disk is off limits, not just that partition.
    """
    for source, label in IN_USE_SOURCES:
        try:
            text = Path(source).read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            fields = line.split()
            if fields and fields[0].startswith(dev):
                return label or (fields[1] if len(fields) > 1 else dev)
    return None


def live_medium() -> str:
    """/dev/<disk> the live ISO booted from, or "" when it cannot be resolved.

    Empty is not "safe": it means the question was not answered, and the
    caller treats it as such.
    """
    name = backing_disk(ARCHISO_MOUNT)
    return f"/dev/{name}" if name else ""


def refuse(dev: str) -> str | None:
    """The reason `dev` must not be touched, or None.

    One place, so that every destructive surface refuses for the same
    reasons in the same order.
    """
    if (where := busy(dev)) is not None:
        return t("blockdev.busy", dev=dev, where=where)
    if (live := live_medium()) and dev == live:
        return t("blockdev.live_medium", dev=dev)
    return None
