"""Swap file hibernation configuration.

Ported from installarchde's swap_file_config with fixes:
- resume=UUID / resume_offset go through core.bootloader, so they land
  in the right place for UKI, systemd-boot entries, GRUB or rEFInd
  (the old code wrote /etc/kernel/cmdline unconditionally),
- stale resume parameters are replaced instead of duplicated,
- the mkinitcpio "resume" hook is only added on busybox initramfs; the
  systemd hook resumes on its own (per Arch Wiki), so it is skipped,
- mkinitcpio -P runs once at the end.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import bootloader, gpuconfig, i18n, mkinitcpio
from .pacman import run
from .prompt import ask_yes
from .sysedit import sudo_write

t = i18n.t

SWAPFILE = "/swapfile"
IMAGE_SIZE = Path("/sys/power/image_size")


def _swapfile_active() -> bool:
    out = subprocess.run(
        ["swapon", "--show=NAME", "--noheadings"], capture_output=True, text=True
    )
    return SWAPFILE in out.stdout.split()


def _swap_uuid() -> str:
    out = subprocess.run(
        ["findmnt", "-no", "UUID", "-T", SWAPFILE], capture_output=True, text=True
    )
    return out.stdout.strip()


def _swap_offset() -> str:
    out = subprocess.run(
        ["sudo", "filefrag", "-v", SWAPFILE], capture_output=True, text=True
    )
    for line in out.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == "0:":
            return fields[3].rstrip(".:")
    return ""


def _swap_bytes() -> int:
    """Every active swap area, not just the swapfile.

    The image goes wherever the kernel finds room, so a machine with a swap
    partition alongside the file is not short just because the file is.
    """
    out = subprocess.run(
        ["swapon", "--show=SIZE", "--bytes", "--noheadings"],
        capture_output=True,
        text=True,
    )
    total = 0
    for line in out.stdout.split():
        try:
            total += int(line)
        except ValueError:
            continue
    return total


def _image_size() -> int:
    try:
        return int(IMAGE_SIZE.read_text())
    except (OSError, ValueError):
        return 0


def _swap_is_big_enough() -> bool:
    """Ask, on a measured negative, before promising hibernation.

    /sys/power/image_size is the kernel's own target for the image, 2/5 of
    RAM by default (measured here: 0.396). Swap smaller than that number
    cannot hold even the image the kernel is aiming to produce -- and the
    failure is not a refusal at hibernate time. Measured on this laptop:
    swap 8.0 GiB against a 10.76 GiB target, systemd's own precheck passed
    because memory happened to be mostly free, the kernel entered
    hibernation, and the machine cold-booted 2.5 minutes later.

    A question rather than a refusal: a machine kept deliberately lean can
    still hibernate, and the caller may only want the resume parameters
    written. Non-interactive runs read as "no", which is the safe direction
    for a task whose failure mode is a lost session.
    """
    swap, image = _swap_bytes(), _image_size()
    if not swap or not image or swap >= image:
        return True
    print(t("msg.swap_too_small", swap=swap // 2**20, image=image // 2**20))
    if ask_yes(t("msg.swap_continue_q")):
        return True
    print(t("msg.cancelled"))
    return False


def _ensure_resume_hook() -> bool:
    """Add the resume hook before fsck on busybox initramfs; True if changed."""
    text = gpuconfig.MKINITCPIO.read_text(encoding="utf-8")
    hooks = mkinitcpio.read_array(text, "HOOKS")
    if hooks is None:
        print(t("msg.hooks_missing"))
        return False

    if "systemd" in hooks:
        print(t("msg.resume_systemd"))
        return False
    if "resume" in hooks:
        print(t("msg.resume_hook_present"))
        return False

    if "fsck" in hooks:
        hooks.insert(hooks.index("fsck"), "resume")
    else:
        hooks.append("resume")
    new_text = mkinitcpio.set_array(text, "HOOKS", hooks)
    return new_text is not None and sudo_write(gpuconfig.MKINITCPIO, new_text) == 0


def configure() -> int:
    if not Path(SWAPFILE).is_file() or not _swapfile_active():
        print(t("msg.swapfile_missing"))
        return 1

    if shutil.which("filefrag") is None:
        if run(["sudo", "pacman", "-S", "--needed", "e2fsprogs"]) != 0:
            return 1

    if not _swap_is_big_enough():
        return 0

    uuid = _swap_uuid()
    offset = _swap_offset()
    if not uuid or not offset.isdigit():
        print(t("msg.swap_params_failed"))
        return 1

    params = [f"resume=UUID={uuid}", f"resume_offset={offset}"]
    result = bootloader.add_kernel_params(
        params, replace_prefixes=("resume=", "resume_offset=")
    )
    hooks_changed = _ensure_resume_hook()

    rc = 0
    if result.needs_mkinitcpio or hooks_changed:
        rc = mkinitcpio.regenerate()
    if result.regen_cmd is not None:
        rc |= run(list(result.regen_cmd))

    if rc == 0:
        print(t("msg.hibernate_done", params=" ".join(params)))
    return rc
