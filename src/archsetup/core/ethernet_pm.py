"""PCI runtime power management for the Realtek 2.5GbE controller.

The kernel leaves ``power/control`` at ``on`` for this NIC and nothing on an
Arch system moves it: tlp is not installed and power-profiles-daemon does not
touch PCI runtime PM. With ``auto`` the controller autosuspends about 10 s
after the link goes down -- on a laptop running on wifi, nearly all the time.

Measured on the G513RM (RTL8125B, XID 641, driver r8169, 10ec:8125): 1.52 W
less at idle, A/B/A/B, repeat spread 0.15-0.24 W, so the difference is larger
than the noise. Against a ~12.8 W floor that is roughly 35 minutes of battery,
which makes it the second largest lever on this machine's power budget.

The wake path was measured too, and it came back the other way round. Every
ingredient for a PME wake is in place: the device suspends 10.0 s after the
link drops and carries ``power/wakeup=enabled`` while suspended, the
controller reports ``PME(D3hot+,D3cold+)``, and its root port carries
``PMEIntEna+``. A cable into a live router still did not wake it. Sampled at
5 ms for 107 s with the cable in there was no transition at all, ``carrier``
stayed 0, and the boot held no ``Link is Up`` line; forcing the resume brought
the link up 2.6 s later, which is what proved the far end had been live the
whole time.

So the accepted risk is not a delay, it is the port staying dead until
something else wakes the device -- and 0.17 s was only ever the forced-resume
figure, which says nothing about cable detection. That is one observation,
with repeats and a control arm still outstanding. The bit that would explain
it -- whether PME-Enable is actually set while suspended -- cannot be read
without a config-space access, and that access resumes the device, so the
reading destroys the state it is trying to report. If the ethernet port is
ever dead, this rule is the first thing to look at: undoing it for now is one
write to ``power/control``, and undoing it permanently is removing the file.
"""

from __future__ import annotations

from pathlib import Path

from . import hardware, i18n, sysedit
from .pacman import run
from .prompt import ask_yes

t = i18n.t

PCI_DEVICES = Path("/sys/bus/pci/devices")

# Only the ID that was measured. r8169 also drives 8126 (5GbE) and the older
# 8168 parts; whether they idle the same way is unknown here, and a udev rule
# is not the place to guess.
VENDOR = "0x10ec"
DEVICE = "0x8125"

UDEV_RULES = Path("/etc/udev/rules.d/81-ethernet-pm.rules")

# Same shape as 80-nvidia-pm.rules: "add|bind" because the coldplug event
# fires before the driver binds and the bind event is what follows it, and
# TEST== so a device without the attribute is skipped instead of erroring.
# No "unbind" counterpart -- nothing here hands the NIC to another driver, and
# a rule for a flow that does not exist is weight nobody tests.
#
# The sibling .bak that write_with_backup leaves behind is harmless in this
# directory: udev reads only files ending in .rules, so a .rules.bak is not a
# second rule. (In libvirt's hooks/ directory the same habit produced exactly
# that, hence the backup= parameter -- the rule of the directory decides.)
UDEV_CONTENT = """\
# Written by archsetup.
# Runtime PM for the Realtek 2.5GbE controller: it autosuspends ~10 s after the
# link drops, and a suspended PHY does not notice a cable plugged back in --
# measured, not assumed. Battery measurement and the accepted risk are in
# core/ethernet_pm.py.
ACTION=="add|bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10ec", ATTR{device}=="0x8125", TEST=="power/control", ATTR{power/control}="auto"
"""


def _attr(device: Path, name: str) -> str:
    try:
        return (device / name).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def matching_devices() -> list[Path]:
    """The devices the rule acts on, matched on the attributes it matches on.

    Reading sysfs rather than lspci keeps the task and the rule looking at the
    same thing: if this list is empty, the rule has nothing to fire on either.
    """
    try:
        entries = sorted(PCI_DEVICES.iterdir())
    except OSError:
        return []
    return [
        device
        for device in entries
        if _attr(device, "vendor") == VENDOR and _attr(device, "device") == DEVICE
    ]


def configure() -> int:
    devices = matching_devices()
    if not devices:
        print(t("ethernet_pm.no_device"))
        return 1
    if not _chassis_ok():
        return 0

    rc, changed = sysedit.write_with_backup(UDEV_RULES, UDEV_CONTENT)
    if changed:
        rc |= run(["sudo", "udevadm", "control", "--reload-rules"])

    # Written is not in force. The rule only runs on a matching event, so we
    # raise one now and then read the attribute back, instead of reporting the
    # bytes we just wrote as if they were the state of the machine.
    rc |= run(["sudo", "udevadm", "trigger", "--action=add", *map(str, devices)])
    rc |= run(["udevadm", "settle"])

    applied = True
    for device in devices:
        control = _attr(device, "power/control")
        print(
            t(
                "ethernet_pm.state",
                device=device.name,
                control=control or "?",
                status=_attr(device, "power/runtime_status") or "?",
            )
        )
        applied = applied and control == "auto"

    if not applied:
        print(t("ethernet_pm.not_applied", path=UDEV_RULES))
        return rc | 1

    print(t("ethernet_pm.done"))
    return rc


def _chassis_ok() -> bool:
    """The whole payoff is battery, so a desktop is asked rather than told.

    With a cable in it the link never drops, the device never autosuspends and
    the rule buys nothing; pull the cable and the measured failure -- a
    suspended PHY that never notices the cable coming back -- becomes that
    machine's problem in exchange for nothing. That is a warning and not a
    refusal: an unknown chassis is not a desktop, and a desktop sitting on
    wifi with an empty port is exactly where the gain would show up.
    """
    laptop = hardware.is_laptop()
    if laptop is True:
        return True

    key = "ethernet_pm.not_laptop" if laptop is False else "ethernet_pm.chassis_unknown"
    print(t(key, chassis=hardware.chassis() or "?"))
    if ask_yes(t("ethernet_pm.continue_q")):
        return True
    print(t("msg.cancelled"))
    return False
