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

import re
import shutil
import subprocess
from pathlib import Path

from . import bootloader, gpuconfig, i18n
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

SWAPFILE = "/swapfile"


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


def _ensure_resume_hook() -> bool:
    """Add the resume hook before fsck on busybox initramfs; True if changed."""
    text = gpuconfig.MKINITCPIO.read_text(encoding="utf-8")
    match = re.search(r"^HOOKS=\(([^)]*)\)", text, re.MULTILINE)
    if match is None:
        print(t("msg.hooks_missing"))
        return False

    hooks = match.group(1).split()
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
    new_text = f"{text[:match.start(1)]}{' '.join(hooks)}{text[match.end(1):]}"
    return sudo_write(gpuconfig.MKINITCPIO, new_text) == 0


def configure() -> int:
    if not Path(SWAPFILE).is_file() or not _swapfile_active():
        print(t("msg.swapfile_missing"))
        return 1

    if shutil.which("filefrag") is None:
        if run(["sudo", "pacman", "-S", "--needed", "e2fsprogs"]) != 0:
            return 1

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
        rc = run(["sudo", "mkinitcpio", "-P"])
    if result.regen_cmd is not None:
        rc |= run(list(result.regen_cmd))

    if rc == 0:
        print(t("msg.hibernate_done", params=" ".join(params)))
    return rc
