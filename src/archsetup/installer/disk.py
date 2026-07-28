"""Disk selection, formatting and mounting (installer mode, runs as root).

Ported from installarch with fixes:
- the btrfs root subvolume flow actually runs (the old script compared
  the label "Root" against lowercase "root", so it was dead code),
- mkfs.fat is forced to FAT32 (-F 32) for the ESP,
- reiserfs is gone (removed from the kernel).

Destructive operations refuse to run outside the live ISO unless
ARCHSETUP_UNSAFE=1 is set (used by tests).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..core import env, i18n
from ..core.pacman import run
from ..core.prompt import ask_yes
from .state import state

t = i18n.t

MNT = Path("/mnt")

# GPT type GUID / MBR type byte of an EFI System Partition.
ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
ESP_MBR = "0xef"

BOOT_FS = ("fat32", "ext4", "ext3", "ext2")
ROOT_FS = ("btrfs", "ext4", "ext3", "ext2", "xfs", "f2fs", "jfs")
FS_PACKAGES = {
    "fat32": "dosfstools",
    "btrfs": "btrfs-progs",
    "xfs": "xfsprogs",
    "f2fs": "f2fs-tools",
    "jfs": "jfsutils",
}


def guard() -> bool:
    if env.is_archiso() or os.environ.get("ARCHSETUP_UNSAFE") == "1":
        return True
    print(t("inst.not_iso"))
    return False


def is_efi() -> bool:
    return Path("/sys/firmware/efi").exists()


def list_devices(kind: str | None = None) -> list[tuple[str, str, str]]:
    out = subprocess.run(
        ["lsblk", "-p", "-n", "-l", "-o", "NAME,SIZE,TYPE", "-e", "7,11"],
        capture_output=True,
        text=True,
    )
    rows = []
    for line in out.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and (kind is None or fields[2] == kind):
            rows.append((fields[0], fields[1], fields[2]))
    return rows


def _choose(title: str, rows: list, allow_none: bool = False) -> str | None:
    """Pick a device by number. None = explicit 'none'; '' = cancelled."""
    print(f"\n{title}")
    if allow_none:
        print("   0) -")
    for index, (name, size, typ) in enumerate(rows, 1):
        print(f"  {index:2}) {name}  ({size}, {typ})")
    while True:
        raw = input(f"{t('inst.choice')}: ").strip()
        if raw == "":
            return ""
        if raw == "0" and allow_none:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(rows):
            return rows[int(raw) - 1][0]
        print(t("inst.invalid"))


def _lsblk_value(dev: str, column: str) -> str:
    out = subprocess.run(
        ["lsblk", "-dn", "-o", column, dev], capture_output=True, text=True
    )
    return out.stdout.strip().lower()


def _partition_location(dev: str) -> tuple[str, str] | None:
    """(parent disk, partition number) — what sfdisk needs to retype it."""
    sysfs = Path("/sys/class/block") / Path(dev).name
    try:
        number = (sysfs / "partition").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return f"/dev/{sysfs.resolve().parent.name}", number


def ensure_esp_type(dev: str) -> int:
    """Check the boot partition's type and offer to correct it.

    A FAT32 partition left as "Linux filesystem" formats, mounts and takes
    files without complaint, so nothing goes wrong until the very last
    step: `bootctl install` validates the GPT type GUID and refuses, and
    firmware that only scans ESP-typed partitions never finds the loader
    even if the files are there. Catching it at selection time costs one
    sfdisk call; catching it after pacstrap costs a reinstall.
    """
    ptype = _lsblk_value(dev, "PARTTYPE")
    if ptype in (ESP_GUID, ESP_MBR):
        return 0

    print(t("inst.esp_wrong_type", dev=dev, type=ptype or "-"))
    location = _partition_location(dev)
    if location is None:
        print(t("inst.esp_no_fix", dev=dev))
        return 1
    if not ask_yes(t("inst.esp_fix_q", dev=dev)):
        print(t("inst.esp_left_alone"))
        return 1

    parent, number = location
    # The type is written per partition-table format: a GUID on GPT, a
    # single type byte on MBR.
    wanted = ESP_MBR if _lsblk_value(dev, "PTTYPE") == "dos" else ESP_GUID
    return run(["sfdisk", "--part-type", parent, number, wanted])


def run_cfdisk() -> int:
    if not guard():
        return 1
    disks = list_devices("disk")
    dev = _choose(t("inst.pick_disk"), disks)
    if not dev:
        return 0
    return subprocess.call(["cfdisk", dev])


def select_partitions() -> int:
    rows = list_devices()
    if not rows:
        print(t("inst.no_devices"))
        return 1

    boot = _choose(t("inst.pick_boot"), rows, allow_none=True)
    if boot == "":
        return 1
    swap = _choose(t("inst.pick_swap"), rows, allow_none=True)
    if swap == "":
        return 1
    root = _choose(t("inst.pick_root"), rows)
    if not root:
        return 1
    home = _choose(t("inst.pick_home"), rows, allow_none=True)
    if home == "":
        return 1

    print(
        f"\nboot : {boot or '-'}\nswap : {swap or '-'}\n"
        f"root : {root}\nhome : {home or '-'}\n"
    )
    if not ask_yes(t("inst.confirm_selection")):
        print(t("msg.cancelled"))
        return 1

    state.bootdev, state.swapdev, state.rootdev, state.homedev = boot, swap, root, home
    if boot and is_efi():
        # Advisory: a wrong type is worth fixing now, but refusing the
        # whole selection over it would strand anyone who knows better.
        ensure_esp_type(boot)
    return 0


def _pick_fs(role: str, dev: str, choices: tuple[str, ...]) -> str:
    print(f"\n{t('inst.fs_for', role=role, dev=dev)}")
    for index, fs in enumerate(choices, 1):
        print(f"  {index}) {fs}")
    while True:
        raw = input(f"{t('inst.choice')}: ").strip()
        if raw == "":
            return ""
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print(t("inst.invalid"))


def _mkfs(fs: str, label: str, dev: str) -> int:
    commands = {
        "fat32": ["mkfs.fat", "-F", "32", "-n", label.upper(), dev],
        "ext2": ["mkfs.ext2", "-L", label, dev],
        "ext3": ["mkfs.ext3", "-L", label, dev],
        "ext4": ["mkfs.ext4", "-L", label, dev],
        "btrfs": ["mkfs.btrfs", "-L", label, "-f", dev],
        "xfs": ["mkfs.xfs", "-L", label, "-f", dev],
        "f2fs": ["mkfs.f2fs", "-l", label, "-f", dev],
        "jfs": ["mkfs.jfs", "-L", label, "-q", dev],
    }
    if fs in FS_PACKAGES and FS_PACKAGES[fs] not in state.fs_packages:
        state.fs_packages.append(FS_PACKAGES[fs])
    return run(commands[fs])


def _format_one(role: str, dev: str, choices: tuple[str, ...]) -> int:
    fs = _pick_fs(role, dev, choices)
    if not fs:
        return 0
    rc = _mkfs(fs, role, dev)
    if rc == 0 and fs == "btrfs" and role == "root":
        rc |= run(["mount", dev, str(MNT)])
        rc |= run(["btrfs", "subvolume", "create", f"{MNT}/root"])
        rc |= run(["btrfs", "subvolume", "set-default", f"{MNT}/root"])
        rc |= run(["umount", str(MNT)])
    return rc


def format_devices() -> int:
    if not guard():
        return 1
    if state.rootdev is None:
        print(t("inst.no_selection"))
        return 1

    devices = [d for d in (state.bootdev, state.swapdev, state.rootdev, state.homedev) if d]
    print(t("inst.format_warning"))
    print("  " + "  ".join(devices))
    if not ask_yes(t("inst.format_q")):
        print(t("msg.cancelled"))
        return 1

    rc = 0
    if state.bootdev:
        rc |= _format_one("boot", state.bootdev, BOOT_FS)
    if state.swapdev:
        rc |= run(["mkswap", "-L", "swap", state.swapdev])
    rc |= _format_one("root", state.rootdev, ROOT_FS)
    if state.homedev:
        rc |= _format_one("home", state.homedev, ROOT_FS)
    return rc


def mount_all() -> int:
    if not guard():
        return 1
    if state.rootdev is None:
        print(t("inst.no_selection"))
        return 1

    rc = run(["mount", state.rootdev, str(MNT)])
    if rc != 0:
        return rc
    (MNT / "efi").mkdir(exist_ok=True)
    (MNT / "home").mkdir(exist_ok=True)
    if state.bootdev:
        rc |= run(["mount", state.bootdev, f"{MNT}/efi"])
    if state.swapdev:
        rc |= run(["swapon", state.swapdev])
    if state.homedev:
        rc |= run(["mount", state.homedev, f"{MNT}/home"])
    return rc


def unmount_all() -> int:
    rc = run(["umount", "-R", str(MNT)])
    if state.swapdev:
        rc |= run(["swapoff", state.swapdev])
    return rc
