"""GPU kernel module configuration (mkinitcpio.conf, modprobe.d, kernel cmdline).

Ported from installarchde's configure_{amd,nvidia}_modules with fixes:
- modules are merged one by one (the old whole-string grep re-appended
  all modules when only some were present),
- the kernel cmdline parameter is nvidia_drm.modeset=1 (the old bare
  "modeset=1" had no effect and false-matched existing entries),
- mkinitcpio -P runs once at the end and only when something changed.

The cmdline step only applies to systemd-boot/UKI setups that use
/etc/kernel/cmdline; GRUB users still configure kernel parameters manually.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import i18n
from .pacman import run

t = i18n.t

MKINITCPIO = Path("/etc/mkinitcpio.conf")
KERNEL_CMDLINE = Path("/etc/kernel/cmdline")
NVIDIA_MODPROBE = Path("/etc/modprobe.d/nvidia.conf")

NVIDIA_MODULES = ("nvidia", "nvidia_modeset", "nvidia_uvm", "nvidia_drm")
AMD_MODULES = ("amdgpu", "radeon")
NVIDIA_CMDLINE_PARAM = "nvidia_drm.modeset=1"
NVIDIA_MODPROBE_CONTENT = "options nvidia_drm modeset=1 fbdev=1\n"


def _sudo_write(path: Path, content: str) -> int:
    print(f"\033[1;36m$ sudo tee {path}\033[0m")
    proc = subprocess.run(
        ["sudo", "tee", str(path)],
        input=content,
        text=True,
        stdout=subprocess.DEVNULL,
    )
    return proc.returncode


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
    return _sudo_write(MKINITCPIO, new_text) == 0


def _ensure_cmdline_param(param: str) -> bool:
    """Append a kernel parameter to /etc/kernel/cmdline (systemd-boot/UKI)."""
    if not KERNEL_CMDLINE.is_file():
        return False
    cmdline = KERNEL_CMDLINE.read_text(encoding="utf-8").strip()
    if not cmdline:
        return False
    if param in cmdline.split():
        print(t("msg.param_present", param=param))
        return False
    return _sudo_write(KERNEL_CMDLINE, f"{cmdline} {param}\n") == 0


def configure_nvidia_modules() -> int:
    changed = _merge_modules(NVIDIA_MODULES)
    if not NVIDIA_MODPROBE.is_file():
        changed |= _sudo_write(NVIDIA_MODPROBE, NVIDIA_MODPROBE_CONTENT) == 0
    changed |= _ensure_cmdline_param(NVIDIA_CMDLINE_PARAM)
    if not changed:
        print(t("msg.no_changes"))
        return 0
    return run(["sudo", "mkinitcpio", "-P"])


def configure_amd_modules() -> int:
    if not _merge_modules(AMD_MODULES):
        print(t("msg.no_changes"))
        return 0
    return run(["sudo", "mkinitcpio", "-P"])
