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
controller reports ``PME(D3hot+,D3cold+)``, its root port carries
``PMEIntEna+``, and ``PME-Enable`` is measurably set in the sleeping device's
own config space. A cable into a live router still does not wake it.

Four observations now, and a control arm that separates the sleeping PHY from
everything else around it. With ``power/control=auto`` the cable sat in a live
port for 107 s once and for 60 s three more times, sampled at 5 ms, and
produced no transition at all: ``carrier`` stayed 0, ``runtime_status`` stayed
``suspended``, and the boot held no ``Link is Up`` line. Forcing the resume
took 0.141-0.181 s and the link followed 2.6-3.3 s later, which is what proves
the far end was live the whole time. With ``power/control=on`` -- same cable,
same router, same interface, same human protocol -- the link came up in all
three rounds *before the operator could report the plug*, 7.7 to 15 s ahead of
it, and the device did not suspend once. The failure is the sleep, not the
port.

So the accepted risk is not a delay, it is the port staying dead until
something else wakes the device; 0.17 s was only ever the forced-resume
figure, which says nothing about cable detection. What is still unexplained is
why: every documented prerequisite for the wake is in place and the wake does
not happen, which leaves the driver never arming a link change as a PME source
as the remaining suspect -- not tested here.

Reading that last bit turned out to cost nothing, against the claim this file
used to make that it could not be read at all. The device sleeps in D3hot, not
D3cold, and config registers stay accessible there: ``lspci -vv`` printed
``PME-Enable+`` while the 5 ms sampler recorded no transition and
``runtime_active_time`` did not move by a millisecond. The observation does
not destroy the state it reports, and an inference that it would -- drawn from
a log pair that merely looked like a resume -- stood in this docstring for a
day.

If the ethernet port is ever dead, this rule is the first thing to look at:
undoing it for now is one write to ``power/control``, and undoing it
permanently is removing the file.
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


def status() -> str:
    """One menu line: what is in force right now, both halves of it.

    Two bits, not one. ``power/control`` is what the controller is doing this
    second; the rule file is whether that survives the next add|bind event.
    They disagree in exactly the two states this module's own tasks pass
    through -- the rule gone while the device is still parked at ``auto``, and
    the attribute written while the rule is still loaded -- and collapsing
    them into one word would hide the half that decides the next boot.

    Read-only and unprivileged: sysfs attributes plus one stat(2). Nothing
    here reaches config space, so it cannot resume a sleeping device and turn
    the reading into the thing it was supposed to report.
    """
    devices = matching_devices()
    if not devices:
        return t("ethernet_pm.status_no_device")

    controls = [_attr(device, "power/control") or "?" for device in devices]
    rule = UDEV_RULES.exists()
    if rule and all(control == "auto" for control in controls):
        verdict = t("ethernet_pm.status_on")
    elif not rule and all(control == "on" for control in controls):
        verdict = t("ethernet_pm.status_off")
    else:
        verdict = t("ethernet_pm.status_split")
    return t(
        "ethernet_pm.status_line",
        verdict=verdict,
        control=", ".join(controls),
        rule=t("ethernet_pm.status_rule_yes" if rule else "ethernet_pm.status_rule_no"),
    )


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


def disable() -> int:
    """Hand the port back: drop the rule, and put power/control back to on.

    Deleting the file is the smaller half, and on its own it is the kind of
    change that reports itself as done without being done. udev rules run on
    events, so a removed rule says nothing to a controller already parked at
    ``auto``: the machine would go on autosuspending until the next boot while
    the task claimed otherwise. Writing the attribute is therefore not a
    belt-and-braces extra but the half that takes effect, and it is the same
    one write that recovers a port found dead.

    The order matters and is the one that survives an interruption. The rule
    goes first, because writing ``on`` while the rule is still loaded leaves a
    window where any matching event puts ``auto`` straight back -- and if the
    run dies in that window, it dies having changed nothing rather than having
    left a rule that disagrees with the device.

    The sibling ``.rules.bak`` from write_with_backup stays. udev reads only
    names ending in ``.rules``, so it is inert where it sits, and throwing
    away the backup that the enabling task deliberately made is not this
    task's call to make.

    What this gives up is measured, not guessed: 1.52 W at idle, about
    35 minutes of battery. That is why it asks first, and why the message at
    the end names the task that puts it back.
    """
    devices = matching_devices()
    if not devices:
        print(t("ethernet_pm.no_device"))
        return 1

    rule_present = UDEV_RULES.exists()
    suspending = [
        device for device in devices if _attr(device, "power/control") != "on"
    ]
    if not rule_present and not suspending:
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

    # Read back for the same reason configure() does: what was written and
    # what the device carries are two different claims.
    restored = True
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
        restored = restored and control == "on"

    if not restored:
        print(t("ethernet_pm.not_restored", path=UDEV_RULES))
        return rc | 1

    print(t("ethernet_pm.disabled"))
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
