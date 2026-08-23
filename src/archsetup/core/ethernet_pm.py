"""Ethernet power saving that does not cost the port.

The lever is PCI runtime power management, and it has two halves because the
hardware turned out to have two. The kernel leaves ``power/control`` at ``on``
for this NIC and nothing on an Arch system moves it -- tlp is not installed and
power-profiles-daemon does not touch PCI runtime PM -- so the untouched machine
is the expensive one. With ``auto`` the controller autosuspends about 10 s
after the link goes down, which on a laptop running on wifi is nearly all the
time, and that is worth a measured 1.52 W.

What it used to cost: a suspended PHY never noticed a cable plugged into a live
port. Four observations plus three repeats with a wake source armed, ~527 s of
cable-in sleep sampled at 5 ms, and not one carrier transition. Forcing the
resume took 0.14-0.18 s and the link followed ~3.3 s later, which is what
proves the far end was live the whole time.

The reason was measured on 2026-08-23 and it is not in this device at all. The
NIC does see the cable and does assert PME: read from config space while it
slept, ``PME-`` became ``PME+`` with the cable in and nothing else changed, and
the read did not resume it (D3hot keeps config registers accessible). The PME
even reaches the root port, which latches it -- ``RootSta: PME ReqID 0400,
PMEStatus+ PMEPending+`` -- and never clears it. Nothing is misconfigured
around it either: ``_OSC`` hands PME control to the OS, ``pcie_pme`` is bound
to that port, the cmdline carries no pci parameters, and the port's PME
interrupt fired once in an entire boot. The port is asleep too, and a sleeping
root port has nobody to deliver the interrupt.

So the fix is one level up: let the NIC sleep, and keep the ROOT PORT awake.
Measured, same protocol as the other levers on this machine (A/B/A/B on
battery, power_now mean, cable OUT because that is the only working point
where the bridge sleeps at all):

    bridge pinned on   8.96 W        difference 0.64 W
    bridge auto        8.32 W        repeat spread 0.15 / 0.03 W

0.64 W against the 1.52 W that keeping the whole NIC awake costs -- the same
working port for 42% of the price. Rounds D1 and D2 confirm the behaviour it
buys: with the bridge pinned and the NIC still suspended, the link came up
before the operator could report the plug, and D2 did it with Wake-on-LAN
disabled, which is how we know ``ethtool -s wol`` was never part of this.

Hence one rule with two halves and one honest promise. Enabling saves 0.88 W
against the untouched machine AND keeps cable detection; disabling goes back to
exactly what Arch leaves behind. The third combination -- both asleep, 1.52 W
saved, port dead until something else wakes it -- is the state that produced
this whole investigation, and it is deliberately not offered.

If the ethernet port is ever dead, this rule is the first thing to look at:
undoing it for now is two writes, and undoing it permanently is removing the
file.
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

# Same shape as 80-nvidia-pm.rules: "add|bind" because the coldplug event fires
# before the driver binds and the bind event is what follows it, and TEST== so
# a device without the attribute is skipped instead of erroring.
#
# The second half is RUN+= rather than a second ATTR{}= because the attribute
# being written does not belong to the matched device: udev's ATTR{} writes to
# the device the rule matched, and ATTRS{} only matches parents, it cannot
# assign to one. So the parent is reached through its path, derived from
# $devpath rather than written down -- this NIC's address moved once already
# (03:00.0 is the wifi card today), and a hardcoded root port would silently
# pin the wrong device while every read-back still passed.
#
# The sibling .bak that write_with_backup leaves behind is harmless in this
# directory: udev reads only files ending in .rules, so a .rules.bak is not a
# second rule.
UDEV_CONTENT = """\
# Written by archsetup.
# Ethernet power saving in two halves, because the hardware needs both.
#   ATTR: the Realtek 2.5GbE controller autosuspends ~10 s after the link drops.
#   RUN:  its PCIe root port is pinned awake, because a sleeping root port
#         latches the NIC's PME and never delivers the interrupt -- measured,
#         so the suspended PHY never notices a cable. 0.64 W, against 1.52 W
#         for keeping the whole NIC awake.
# Measurements and the rejected third state are in core/ethernet_pm.py.
ACTION=="add|bind", SUBSYSTEM=="pci", ATTR{vendor}=="0x10ec", ATTR{device}=="0x8125", TEST=="power/control", ATTR{power/control}="auto", RUN+="/bin/sh -c 'echo on > /sys$devpath/../power/control'"
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


def bridge_for(device: Path) -> Path | None:
    """The PCIe root port above a NIC, derived the same way the rule derives it.

    ``/sys/bus/pci/devices/<addr>`` is a symlink into /sys/devices, and the
    parent of the resolved path is the port. Returning None rather than a guess
    keeps a machine where that shape does not hold -- a NIC hanging straight
    off the host bridge -- out of the write path instead of pinning something
    arbitrary.
    """
    try:
        parent = device.resolve().parent
    except OSError:
        return None
    if not parent.name.startswith("0000:") or not (parent / "power" / "control").exists():
        return None
    return parent


def _pair(device: Path) -> tuple[str, str]:
    """(NIC control, bridge control) -- the two bits the promise rests on."""
    bridge = bridge_for(device)
    return (
        _attr(device, "power/control") or "?",
        _attr(bridge, "power/control") if bridge else "?",
    )


def status() -> str:
    """One menu line: what is in force right now, all of it.

    Three bits, not one. The NIC's ``power/control`` is what the controller is
    doing this second, the bridge's is whether the port can be woken at all,
    and the rule file is whether either survives the next add|bind event. They
    disagree in exactly the states this module's own tasks pass through, and
    collapsing them would hide the half that decides the next boot.

    Read-only and unprivileged: sysfs attributes plus one stat(2). Nothing here
    reaches config space, so it cannot resume a sleeping device and turn the
    reading into the thing it was supposed to report.
    """
    devices = matching_devices()
    if not devices:
        return t("ethernet_pm.status_no_device")

    pairs = [_pair(device) for device in devices]
    rule = UDEV_RULES.exists()
    saving = all(nic == "auto" and bridge == "on" for nic, bridge in pairs)
    stock = all(nic == "on" and bridge != "on" for nic, bridge in pairs)
    if rule and saving:
        verdict = t("ethernet_pm.status_on")
    elif not rule and stock:
        verdict = t("ethernet_pm.status_off")
    else:
        verdict = t("ethernet_pm.status_split")
    return t(
        "ethernet_pm.status_line",
        verdict=verdict,
        control=", ".join(f"{nic}/{bridge}" for nic, bridge in pairs),
        rule=t("ethernet_pm.status_rule_yes" if rule else "ethernet_pm.status_rule_no"),
    )


def _report(devices: list[Path]) -> list[tuple[str, str]]:
    pairs = []
    for device in devices:
        nic, bridge_control = _pair(device)
        bridge = bridge_for(device)
        print(
            t(
                "ethernet_pm.state",
                device=device.name,
                control=nic,
                status=_attr(device, "power/runtime_status") or "?",
                bridge=bridge.name if bridge else "?",
                bridge_control=bridge_control,
            )
        )
        pairs.append((nic, bridge_control))
    return pairs


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
    # raise one now and then read both attributes back, instead of reporting
    # the bytes we just wrote as if they were the state of the machine. This
    # is also the only measurement of the RUN half short of a reboot: a rule
    # that matches is not a rule whose RUN ran, and `udevadm test` would not
    # tell them apart because it simulates instead of executing.
    rc |= run(["sudo", "udevadm", "trigger", "--action=add", *map(str, devices)])
    rc |= run(["udevadm", "settle"])

    pairs = _report(devices)
    if not all(nic == "auto" and bridge == "on" for nic, bridge in pairs):
        print(t("ethernet_pm.not_applied", path=UDEV_RULES))
        return rc | 1

    print(t("ethernet_pm.done"))
    return rc


def disable() -> int:
    """Hand the machine back to what Arch leaves behind: NIC on, bridge auto.

    Deleting the file is the smaller half, and on its own it is the kind of
    change that reports itself as done without being done. udev rules run on
    events, so a removed rule says nothing to a controller already parked at
    ``auto`` or a port already pinned at ``on``: the machine would go on in the
    old state until the next boot while the task claimed otherwise. Writing the
    attributes is therefore not a belt-and-braces extra but the half that takes
    effect.

    The order matters and is the one that survives an interruption. The rule
    goes first, because writing while it is still loaded leaves a window where
    any matching event puts the old state straight back -- and if the run dies
    in that window, it dies having changed nothing rather than having left a
    rule that disagrees with the device.

    What this gives up is measured, not guessed: 0.88 W at idle against the
    saving state, for no functional difference at all -- cable detection works
    in both. That is why it asks first, and why the message at the end names
    the task that puts it back.
    """
    devices = matching_devices()
    if not devices:
        print(t("ethernet_pm.no_device"))
        return 1

    rule_present = UDEV_RULES.exists()
    # Either half can be the thing left behind, so both are asked about. A NIC
    # already at `on` with its bridge still pinned is not the stock state, and
    # a task that called it one would leave 0.64 W running under a menu line
    # saying "off".
    not_stock = [
        device
        for device in devices
        if _pair(device)[0] != "on" or _pair(device)[1] == "on"
    ]
    if not rule_present and not not_stock:
        print(t("ethernet_pm.already_off"))
        return 0

    print(t("ethernet_pm.disable_plan", path=UDEV_RULES))
    if not ask_yes(t("ethernet_pm.disable_q")):
        print(t("msg.cancelled"))
        return 0

    rc = 0
    if rule_present:
        rc |= run(["sudo", "rm", "-f", str(UDEV_RULES)])
        rc |= run(["sudo", "udevadm", "control", "--reload-rules"])

    for device in devices:
        rc |= sysedit.sudo_write(device / "power" / "control", "on\n")
        bridge = bridge_for(device)
        if bridge is not None:
            rc |= sysedit.sudo_write(bridge / "power" / "control", "auto\n")

    # Read back for the same reason configure() does: what was written and what
    # the devices carry are two different claims.
    pairs = _report(devices)
    if not all(nic == "on" and bridge != "on" for nic, bridge in pairs):
        print(t("ethernet_pm.not_restored", path=UDEV_RULES))
        return rc | 1

    print(t("ethernet_pm.disabled"))
    return rc


def _chassis_ok() -> bool:
    """The whole payoff is battery, so a desktop is asked rather than told.

    With a cable in it the link never drops, the NIC never autosuspends and the
    rule buys nothing -- the bridge does not sleep either, so even the half
    that protects the port has nothing to protect. That is a warning and not a
    refusal: an unknown chassis is not a desktop, and a desktop sitting on wifi
    with an empty port is exactly where the gain would show up.
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
