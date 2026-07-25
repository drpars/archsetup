"""NVIDIA laptop power management for hybrid (Optimus) ROG/TUF notebooks.

Follows the NVIDIA section of https://asus-linux.org/guides/arch-guide/,
which upstream ships as the `nvidia-laptop-power-cfg` package. We write the
same two files ourselves instead of building that package, so the task works
without git/base-devel and stays in step with the rest of archsetup.

What it buys you: without NVreg_EnableS0ixPowerManagement the dGPU is never
powered down on s2idle-only laptops, so a suspended machine keeps draining
the battery, and without nvidia-powerd there is no Dynamic Boost.

Turing (GTX 16xx / RTX 20xx) additionally needs NVreg_EnableGpuFirmware=0;
Ampere and newer must not have it. The generation comes from the codename
lspci prints (TU106M, GA106M, AD107M...); when it cannot be determined we
take the Ampere-and-newer path, which is the safe default on current parts.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import hardware, i18n, pacman, services
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

MODPROBE_CONF = Path("/etc/modprobe.d/nvidia.conf")
UDEV_RULES = Path("/etc/udev/rules.d/80-nvidia-pm.rules")

# Enabled plainly; nvidia-powerd additionally gets --now (see the guide).
SERVICES = (
    "nvidia-suspend.service",
    "nvidia-resume.service",
    "nvidia-hibernate.service",
    "nvidia-suspend-then-hibernate.service",
)
POWERD_SERVICE = "nvidia-powerd.service"

TURING_CODENAME = re.compile(r"\bTU1\d\d", re.IGNORECASE)

# fbdev=0 is deliberate: an fbdev console on the dGPU keeps waking it and
# defeats runtime power management on hybrid laptops.
MODPROBE_TEMPLATE = """\
# Written by archsetup, following https://asus-linux.org/guides/arch-guide/
options nvidia_drm modeset=1 fbdev=0
options nvidia {nvidia_options}
"""

BASE_NVIDIA_OPTIONS = (
    "NVreg_EnableS0ixPowerManagement=1 NVreg_DynamicPowerManagement=0x02"
)
TURING_NVIDIA_OPTION = "NVreg_EnableGpuFirmware=0"

UDEV_CONTENT = """\
# Written by archsetup, following https://asus-linux.org/guides/arch-guide/

# Remove NVIDIA USB xHCI Host Controller devices, if present
ACTION=="add", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x0c0330", ATTR{remove}="1"

# Remove NVIDIA USB Type-C UCSI devices, if present
ACTION=="add", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x0c8000", ATTR{remove}="1"

# Enable runtime PM for NVIDIA VGA/3D controller devices on driver bind
ACTION=="add|bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030000", TEST=="power/control", ATTR{power/control}="auto"
ACTION=="add|bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030200", TEST=="power/control", ATTR{power/control}="auto"

# Disable runtime PM for NVIDIA VGA/3D controller devices on driver unbind
ACTION=="unbind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030000", TEST=="power/control", ATTR{power/control}="on"
ACTION=="unbind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030200", TEST=="power/control", ATTR{power/control}="on"
"""


def is_turing() -> bool:
    for line in hardware.nvidia_gpu_lines():
        if TURING_CODENAME.search(line):
            return True
    return False


def modprobe_content() -> str:
    options = BASE_NVIDIA_OPTIONS
    if is_turing():
        options = f"{options} {TURING_NVIDIA_OPTION}"
    return MODPROBE_TEMPLATE.format(nvidia_options=options)


def _write(path: Path, content: str) -> tuple[int, bool]:
    """Write `content` to `path`, backing up a differing existing file.

    Returns (exit code, changed).
    """
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        current = None

    if current == content:
        print(t("nvidia_laptop.unchanged", path=path))
        return 0, False

    rc = 0
    if current is not None:
        backup = path.with_suffix(path.suffix + ".bak")
        print(t("nvidia_laptop.backup", path=path, backup=backup))
        rc |= run(["sudo", "cp", str(path), str(backup)])

    rc |= sudo_write(path, content)
    return rc, True


def configure() -> int:
    if not hardware.gpu_matches("nvidia"):
        print(t("nvidia_laptop.no_gpu"))
        return 1
    if not pacman.is_installed("nvidia-utils"):
        print(t("nvidia_laptop.driver_missing"))
        return 1

    print(t("nvidia_laptop.turing" if is_turing() else "nvidia_laptop.ampere"))

    rc, modprobe_changed = _write(MODPROBE_CONF, modprobe_content())
    udev_rc, udev_changed = _write(UDEV_RULES, UDEV_CONTENT)
    rc |= udev_rc

    for service in SERVICES:
        if services.unit_exists(service):
            rc |= services.enable(service)
        else:
            print(t("nvidia_laptop.unit_missing", unit=service))
    if services.unit_exists(POWERD_SERVICE):
        rc |= services.enable_now(POWERD_SERVICE)
    else:
        print(t("nvidia_laptop.unit_missing", unit=POWERD_SERVICE))

    if modprobe_changed:
        # Module options are baked into the initramfs on early-KMS setups.
        rc |= run(["sudo", "mkinitcpio", "-P"])
    if udev_changed:
        rc |= run(["sudo", "udevadm", "control", "--reload-rules"])

    print(t("nvidia_laptop.done" if modprobe_changed else "nvidia_laptop.done_nochange"))
    return rc
