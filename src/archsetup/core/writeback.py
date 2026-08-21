"""Dirty page limits: an honest copy to a slow disk, and a lower global ceiling.

A write() to a USB stick returns as soon as the kernel has the bytes in page
cache, so the copy "finishes" at RAM speed and the wait moves to umount, where
nothing reports it. Measured here: 4 GiB to a USB stick, dd returned in 4.56 s
claiming 943 MB/s with 99.1% of the data still in memory, and the umount that
followed took 218.45 s. The device's real speed was 16.2 MB/s, so the number
the copy reported was 48.9x the truth.

Two knobs. They are not alternatives -- one bounds every disk a little, the
other bounds a slow one completely.

**Per device, the udev rule.** ``strict_limit=1`` plus a ``max_bytes`` ceiling
holds the backing device to its own share instead of letting it borrow the
global one; lowering ``max_ratio`` alone does not bind, the strictlimit branch
in mm/page-writeback.c is what does. Measured with 64 MiB: dirty never once
passed the limit over 1090 samples (peak 27.3 MiB), umount fell from 218.45 s
to 2.32 s -- 94x -- and the total was unchanged, 217.74 s against 223.01 s.
The wait did not go away, it moved into the copy. That is the second gain and
it is free: a file manager's progress bar and task list become honest without
a line of their code changing, which matters because yazi publishes no
"task finished" event to hook a notification onto.

The cost on a fast device was measured too, because the rule cannot tell them
apart: a Kingston SATA SSD in a 5 Gbps enclosure (363 MB/s by O_DIRECT) took
14.02 s with the ceiling against 14.38 s without it. Three speed classes were
measured -- a 16 MB/s stick, a 36 MB/s USB 2.0 link, that 363 MB/s SSD -- and
the ceiling changed the total in none of them.

**Globally, the sysctl file.** ``vm.dirty_ratio=3`` / ``dirty_background_ratio
=2`` is the Arch wiki's own advice for a machine with much more RAM than the
default percentage was written for, and here it was not a cost but a gain:
4 GiB to the internal NVMe went 3547 -> 4714 MB/s (+33%) and the hidden wait
after it 0.40 -> 0.085 s. The wiki's *other* recipe -- 4 MiB of
``vm.dirty_bytes``, under "Small periodic system freezes" -- is deliberately
not used: measured on the same disk it is 4.5x slower (3547 -> 785 MB/s).
Which is why this task prints what the ratio comes to in bytes on the machine
it runs on. The percentage is the setting; the ceiling is what was measured.

The two do not overlap. ``ratio=3`` leaves roughly a 740 MB ceiling here,
which on a 16 MB/s stick is still ~46 s of hidden wait (arithmetic, not
measured); the per-device ceiling is what takes that to 2.32 s.

Why the rule assigns the attributes itself rather than shipping a helper
script the way BigLinux's usb-dirty-pages-udev package does: measured
2026-08-21 against the SSD above, ``ATTR{bdi/...}`` in a rule reaches
/sys/block/sda/bdi/ through its symlink -- strict_limit went 0 -> 1 and
max_bytes to 67083717 on a plain ``udevadm trigger --action=add``. A rule
that needs no second file is worth more than the guard that script carries,
and that guard is answered below instead.

Why ID_USB_TYPE and not ATTR{removable}: that SSD reports ``removable=0``, so
a rule matching removable would have missed exactly the device whose cost was
being worried about. ID_BUS does not work either -- it reads ``ata`` through
that enclosure. ID_USB_TYPE=disk is present on both it and the old stick.

Why max_bytes does not read back verbatim, and why the order of the two halves
matters: the kernel keeps it as a percentage of the *global* dirty limit, not
as bytes. Measured here, writing the same 67108864 twice: under the kernel
default (20% of 27807 MiB = 5561 MiB) it stored max_ratio=1, under the ratio
above (834 MiB) it stored 8. So a per-device ceiling follows the global one
afterwards. configure() applies the sysctl half first for that reason, and the
check below reads strict_limit rather than max_bytes, which the recomputation
leaves tens of kilobytes off either way.

That order is not guaranteed at boot, and the gap was measured rather than
assumed: nothing orders systemd-sysctl.service against udev coldplug, and on
this machine's boot systemd-udev-trigger.service started 0.33 s *before* it. A
USB disk already attached when the machine boots therefore has its ceiling
stored against the kernel default and lands near 8 MiB rather than 64 once the
ratio applies. Bounded, in the direction the measurements found free on a slow
device (BigLinux ships 16 MB unconditionally), unmeasured on a fast one, and
one replug -- or `udevadm trigger --action=add /sys/block/<name>` -- restores
it. No third file is written to force the order: an ordering drop-in on
systemd-udev-trigger.service would reorder every coldplug event on the machine
for this one reason, and a boot-order cycle costs more than 8 MiB does.

**Not measured: this rule on a machine whose root filesystem lives on a USB
disk.** BigLinux's script guards that at event time by excluding the root
device; here the machine is asked once, when the task runs, which is the
weaker guard -- it does not follow a system that is later moved onto an
external disk. The risk is accepted knowingly and it is why the check exists
at all. Also unmeasured: a 10 Gbps link, an NVMe-based enclosure, and
CachyOS's byte-valued alternative (256/64 MiB).
"""

from __future__ import annotations

import subprocess
from fnmatch import fnmatch
from pathlib import Path

from . import hardware, i18n, sysedit
from .pacman import run

t = i18n.t

BLOCK = Path("/sys/block")
PROC_VM = Path("/proc/sys/vm")

SYSCTL_CONF = Path("/etc/sysctl.d/99-dirty-writeback.conf")
# After 60-persistent-storage.rules, which is where ID_USB_TYPE is imported
# from the usb_id builtin: a rule matching on it has to run later than that.
UDEV_RULES = Path("/etc/udev/rules.d/82-usb-dirty-pages.rules")

DIRTY_RATIO = 3
DIRTY_BACKGROUND_RATIO = 2

# 64 MiB: the value measured on all three speed classes. BigLinux ships 16 MB
# and that was measured free of cost too, on the fast device -- but only there.
MAX_BYTES = 64 * 1024 * 1024

# The rule matches whole disks, not partitions: bdi/ hangs off the disk. Kept
# as a constant because the task enumerates the same set to read the result
# back, and two spellings of "which devices" would drift apart.
KERNEL_GLOB = "sd[a-z]"

SYSCTL_CONTENT = f"""\
# Written by archsetup.
# A copy to a slow disk returns long before the bytes reach it and the wait
# lands in umount, where nothing reports it. These lower the ceiling for every
# disk; the per-device half is in {UDEV_RULES}.
# Measurements, and why not the wiki's 4 MiB recipe: core/writeback.py.
vm.dirty_ratio = {DIRTY_RATIO}
vm.dirty_background_ratio = {DIRTY_BACKGROUND_RATIO}
"""

UDEV_CONTENT = f"""\
# Written by archsetup.
# Bind the dirty-page share of USB disks so a copy runs at the device's real
# speed instead of RAM speed. Measured: umount 218.45 s -> 2.32 s with the
# total time unchanged across three speed classes. Numbers, and why the match
# is ID_USB_TYPE rather than removable: core/writeback.py.
ACTION=="add", SUBSYSTEM=="block", KERNEL=="{KERNEL_GLOB}", ENV{{ID_USB_TYPE}}=="disk", TEST=="bdi/strict_limit", ATTR{{bdi/strict_limit}}="1", ATTR{{bdi/max_bytes}}="{MAX_BYTES}"
"""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _out(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return ""
    return proc.stdout.strip()


def udev_property(device: str, name: str) -> str:
    """One property of a block device as udev itself sees it.

    Read through udevadm rather than walked in sysfs for the same reason
    ethernet_pm reads the attributes its rule matches on: this is the value
    the rule is tested against, so an answer here that disagrees with the
    rule would be answering a different question.
    """
    for line in _out(["udevadm", "info", "--query=property", "--name", device]).splitlines():
        key, _, value = line.partition("=")
        if key == name:
            return value
    return ""


def usb_disks() -> list[str]:
    """The attached disks the rule would fire on."""
    try:
        entries = sorted(BLOCK.iterdir())
    except OSError:
        return []
    return [
        device.name
        for device in entries
        if fnmatch(device.name, KERNEL_GLOB)
        and (device / "bdi" / "strict_limit").exists()
        and udev_property(device.name, "ID_USB_TYPE") == "disk"
    ]


def backing_disk(mountpoint: str) -> str:
    """The whole disk under `mountpoint`, or "" when it cannot be resolved.

    Two steps because a mount points at a partition and the rule matches a
    disk: findmnt names the source, lsblk names the parent it belongs to.
    PKNAME is empty for a device that is already whole, and that answer is
    the source itself.
    """
    source = _out(["findmnt", "-no", "SOURCE", "--target", mountpoint])
    if not source or not source.startswith("/dev/"):
        return ""
    parent = _out(["lsblk", "-no", "PKNAME", source]).splitlines()
    if parent and parent[0].strip():
        return parent[0].strip()
    return Path(source).name


def root_on_usb(mountpoint: str = "/") -> bool | None:
    """Would the rule catch the disk this system boots from? None = unknown."""
    disk = backing_disk(mountpoint)
    if not disk:
        return None
    return fnmatch(disk, KERNEL_GLOB) and udev_property(disk, "ID_USB_TYPE") == "disk"


def _mib(value: int) -> int:
    return value // (1024 * 1024)


def apply_sysctl() -> int:
    ram = hardware.ram_bytes()
    current = _read(PROC_VM / "dirty_ratio") or "?"
    if ram and current.isdigit():
        print(
            t(
                "writeback.ceiling",
                ram=_mib(ram),
                current=current,
                current_mib=_mib(ram * int(current) // 100),
                ratio=DIRTY_RATIO,
                ratio_mib=_mib(ram * DIRTY_RATIO // 100),
            )
        )

    # Ratios and bytes are mutually exclusive: writing one zeroes the other.
    # A machine that already carries a byte-valued setting (CachyOS ships
    # one) is losing it here, so it is named rather than silently replaced.
    previous_bytes = _read(PROC_VM / "dirty_bytes")
    if previous_bytes not in ("", "0"):
        print(t("writeback.bytes_cleared", bytes=previous_bytes))

    rc, _ = sysedit.write_with_backup(SYSCTL_CONF, SYSCTL_CONTENT)

    # Written is not in force, and here that is not pedantry: sysctl.d files
    # apply in filename order across directories, so a file sorting after
    # this one wins. --system rather than -p <file> for exactly that reason --
    # it reproduces what the next boot will do, instead of what this file says.
    rc |= run(["sudo", "sysctl", "--system"])

    ratio = _read(PROC_VM / "dirty_ratio")
    background = _read(PROC_VM / "dirty_background_ratio")
    if (ratio, background) != (str(DIRTY_RATIO), str(DIRTY_BACKGROUND_RATIO)):
        print(
            t(
                "writeback.sysctl_not_applied",
                path=SYSCTL_CONF,
                ratio=ratio or "?",
                background=background or "?",
            )
        )
        return rc | 1

    print(t("writeback.sysctl_done", ratio=ratio, background=background))
    return rc


def apply_udev(mountpoint: str = "/") -> int:
    on_usb = root_on_usb(mountpoint)
    if on_usb is None:
        print(t("writeback.root_unknown", mountpoint=mountpoint))
        return 1
    if on_usb:
        print(t("writeback.root_on_usb", disk=backing_disk(mountpoint)))
        return 0

    rc, changed = sysedit.write_with_backup(UDEV_RULES, UDEV_CONTENT)
    if changed:
        rc |= run(["sudo", "udevadm", "control", "--reload-rules"])

    disks = usb_disks()
    if not disks:
        print(t("writeback.no_disk_attached", path=UDEV_RULES))
        return rc

    # The rule only runs on an event, so one is raised and the attribute read
    # back afterwards -- the bytes just written are not the state of anything.
    rc |= run(
        ["sudo", "udevadm", "trigger", "--action=add", *[str(BLOCK / d) for d in disks]]
    )
    rc |= run(["udevadm", "settle"])

    applied = True
    for disk in disks:
        strict = _read(BLOCK / disk / "bdi" / "strict_limit")
        print(
            t(
                "writeback.device_state",
                device=disk,
                strict=strict or "?",
                max_bytes=_read(BLOCK / disk / "bdi" / "max_bytes") or "?",
            )
        )
        applied = applied and strict == "1"

    if not applied:
        print(t("writeback.not_applied", path=UDEV_RULES))
        return rc | 1

    print(t("writeback.udev_done"))
    return rc


def configure() -> int:
    # The sysctl half first, and not for tidiness: max_bytes is stored as a
    # percentage of the global dirty limit, so a rule that fires before the
    # ratio lands stores the wrong one. Measured, see the module docstring.
    return apply_sysctl() | apply_udev()
