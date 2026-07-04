"""GPU kernel module configuration (mkinitcpio.conf, modprobe.d, kernel cmdline).

Ported from installarchde's configure_{amd,nvidia}_modules with fixes:
- modules are merged one by one (the old whole-string grep re-appended
  all modules when only some were present),
- the kernel cmdline parameter is nvidia_drm.modeset=1 (the old bare
  "modeset=1" had no effect and false-matched existing entries),
- mkinitcpio -P runs once at the end and only when something changed.

The kernel parameter goes through core.bootloader, so it lands in the
right place for UKI, classic systemd-boot, GRUB or rEFInd setups.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import bootloader, i18n
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

MKINITCPIO = Path("/etc/mkinitcpio.conf")
NVIDIA_MODPROBE = Path("/etc/modprobe.d/nvidia.conf")

NVIDIA_MODULES = ("nvidia", "nvidia_modeset", "nvidia_uvm", "nvidia_drm")
AMD_MODULES = ("amdgpu", "radeon")
NVIDIA_CMDLINE_PARAM = "nvidia_drm.modeset=1"
NVIDIA_MODPROBE_CONTENT = "options nvidia_drm modeset=1 fbdev=1\n"


def _merge_modules(modules: tuple[str, ...]) -> bool:
    """Add missing entries to MODULES=(...); returns True if the file changed."""
    text = MKINITCPIO.read_text(encoding="utf-8")
    match = re.search(r"^MODULES=\(([^)]*)\)", text, re.MULTILINE)
    if match is None:
        print(t("msg.mkinitcpio_no_modules"))
        return False

    present = match.group(1).split()
    missing = [mod for mod in modules if mod not in present]
    if not missing:
        print(t("msg.modules_present", modules=" ".join(modules)))
        return False

    merged = " ".join(present + missing)
    new_text = f"{text[:match.start(1)]}{merged}{text[match.end(1):]}"
    return sudo_write(MKINITCPIO, new_text) == 0


def _nvidia_modeset_is_default() -> bool:
    """Since nvidia-utils 560.35.03-5, modeset and fbdev default to on."""
    out = subprocess.run(
        ["pacman", "-Q", "nvidia-utils"], capture_output=True, text=True
    )
    if out.returncode != 0:
        return False
    version = out.stdout.split()[1]
    match = re.match(r"(\d+)", version)
    return match is not None and int(match.group(1)) >= 560


def configure_nvidia_modules() -> int:
    changed = _merge_modules(NVIDIA_MODULES)

    if _nvidia_modeset_is_default():
        # No modprobe.d entry or kernel parameter needed on current drivers.
        print(t("msg.modeset_default"))
        result = bootloader.ParamResult(False)
    else:
        if not NVIDIA_MODPROBE.is_file():
            changed |= sudo_write(NVIDIA_MODPROBE, NVIDIA_MODPROBE_CONTENT) == 0
        result = bootloader.add_kernel_params([NVIDIA_CMDLINE_PARAM])

    rc = 0
    if changed or result.needs_mkinitcpio:
        rc = run(["sudo", "mkinitcpio", "-P"])
    if result.regen_cmd is not None:
        rc |= run(list(result.regen_cmd))
    if not changed and not result.changed:
        print(t("msg.no_changes"))
    return rc


def configure_amd_modules() -> int:
    if not _merge_modules(AMD_MODULES):
        print(t("msg.no_changes"))
        return 0
    return run(["sudo", "mkinitcpio", "-P"])
