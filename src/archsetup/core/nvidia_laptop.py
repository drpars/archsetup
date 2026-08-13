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

from . import hardware, i18n, mkinitcpio, pacman, services, sysedit
from .pacman import run
from .prompt import ask_yes

t = i18n.t

MODPROBE_CONF = Path("/etc/modprobe.d/nvidia.conf")
UDEV_RULES = Path("/etc/udev/rules.d/80-nvidia-pm.rules")

# These three preserve VRAM across sleep and are NOT laptop-specific: any
# machine that suspends wants them, desktops included. PreserveVideoMemory-
# Allocations only declares the intent -- the thing that actually writes VRAM
# out is nvidia-sleep.sh, and these units are what call it.
SLEEP_SERVICES = (
    "nvidia-suspend.service",
    "nvidia-resume.service",
    "nvidia-hibernate.service",
)

# Only pulled in when the machine really uses suspend-then-hibernate; on a
# host whose idle action is a plain `systemctl suspend` it never runs.
S2H_SERVICE = "nvidia-suspend-then-hibernate.service"

# Dynamic Boost -- laptop-only, so it stays in the laptop task.
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


def configure() -> int:
    if not hardware.gpu_matches("nvidia"):
        print(t("nvidia_laptop.no_gpu"))
        return 1
    if not pacman.is_installed("nvidia-utils"):
        print(t("nvidia_laptop.driver_missing"))
        return 1
    if not _chassis_ok():
        return 0

    print(t("nvidia_laptop.turing" if is_turing() else "nvidia_laptop.ampere"))

    rc, modprobe_changed = sysedit.write_with_backup(MODPROBE_CONF, modprobe_content())
    udev_rc, udev_changed = sysedit.write_with_backup(UDEV_RULES, UDEV_CONTENT)
    rc |= udev_rc

    rc |= enable_sleep_services(ask_s2h=False)
    if services.unit_exists(S2H_SERVICE):
        rc |= services.enable(S2H_SERVICE)
    else:
        print(t("nvidia_laptop.unit_missing", unit=S2H_SERVICE))
    if services.unit_exists(POWERD_SERVICE):
        rc |= services.enable_now(POWERD_SERVICE)
    else:
        print(t("nvidia_laptop.unit_missing", unit=POWERD_SERVICE))

    if modprobe_changed:
        # Module options are baked into the initramfs on early-KMS setups.
        rc |= mkinitcpio.regenerate()
    if udev_changed:
        rc |= run(["sudo", "udevadm", "control", "--reload-rules"])

    print(t("nvidia_laptop.done" if modprobe_changed else "nvidia_laptop.done_nochange"))
    return rc


def enable_sleep_services(ask_s2h: bool = True) -> int:
    """VRAM'i uykuda koruyan birimleri ac -- dizustu/masaustu farketmez.

    Bunlar archsetup'ta uzun sure yalnizca `nvidia-laptop-power` gorevinin
    icinde aciliyordu; o gorev ise ayni anda S0ix ve DynamicPowerManagement
    gibi **dizustune ozgu** secenekleri de yaziyor. Sonuc: duzenli askiya
    alinan bir masaustunde VRAM korumasini acmanin tek yolu, o makineye
    anlamsiz gelen dizustu ayarlarini da kabul etmekti. Bu yuzden ayrildi.

    nvidia-powerd (Dynamic Boost) burada YOK: o gercekten dizustune ozgu.
    """
    if not hardware.gpu_matches("nvidia"):
        print(t("nvidia_laptop.no_gpu_any"))
        return 1
    if not pacman.is_installed("nvidia-utils"):
        print(t("nvidia_laptop.driver_missing"))
        return 1

    rc = 0
    for service in SLEEP_SERVICES:
        if services.unit_exists(service):
            rc |= services.enable(service)
        else:
            print(t("nvidia_laptop.unit_missing", unit=service))

    # suspend-then-hibernate ayri bir uyku bicimi. Kullanilmiyorsa birim hic
    # calismaz; acmak zararsiz ama yanlis bir izlenim birakir, o yuzden
    # sorulur -- makinenin bos kalinca ne yaptigini kullanici bilir.
    if ask_s2h and services.unit_exists(S2H_SERVICE):
        if ask_yes(t("nvidia_laptop.s2h_q")):
            rc |= services.enable(S2H_SERVICE)

    if rc == 0:
        print(t("nvidia_laptop.sleep_done"))
    return rc


def _chassis_ok() -> bool:
    """Bu gorev bir dizustu recetesi; masaustunde her maddesi yanlis.

    Onceden gorevi engelleyen tek sey menu basligiydi. NVIDIA'li bir
    masaustunde secilirse yazilanlar:

      * ``fbdev=0`` -- tek kartli makinede konsolu surecek baska GPU yok
      * S0ix + ``NVreg_DynamicPowerManagement`` -- s2idle dizustu ayarlari
      * Optimus runtime-PM udev kurallari -- hibrit olmayan makinede karsiligi yok
      * ``nvidia-powerd --now`` -- Dynamic Boost, yalniz dizustunde var

    Sasi bilinmiyorsa (None) reddetmiyoruz: canli ISO gibi ortamlarda
    hostnamectl cevap vermeyebilir ve "bilmiyorum" ile "masaustu" ayni sey
    degil. Iki durumda da karar kullanicinin, ama uyari acik.
    """
    laptop = hardware.is_laptop()
    if laptop is True:
        return True

    key = "nvidia_laptop.not_laptop" if laptop is False else "nvidia_laptop.chassis_unknown"
    print(t(key, chassis=hardware.chassis() or "?"))
    print(t("nvidia_laptop.sleep_hint"))
    if ask_yes(t("nvidia_laptop.continue_q")):
        return True
    print(t("msg.cancelled"))
    return False
