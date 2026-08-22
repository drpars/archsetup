"""802.11 power save on the Intel card: the knob, and the price on its label.

Two measurements meet here and neither was taken for this module. Turning
power save off makes the link about five times quicker to answer: average RTT
36-45 ms with it on against 7.8 ms with it off, and the jitter collapses from
21-29 ms to 1.6 ms -- measured 2026-08-17 while chasing a remote desktop that
felt slow, and the finding outlived the remote desktop because it touches
every round trip the machine makes. Turning it off also costs 0.50 W at idle,
about 17 minutes on a 65.9 Wh pack -- A/B/A/B, repeat spread 0.01-0.10 W,
measured 2026-08-22. Both live in the pars notes (uzak-masaustu and archsetup
respectively); neither is repeated here beyond the two numbers the user has
to see before choosing.

So this is a pair of tasks rather than a recommendation. The kernel default is
power save ON: the battery gain is already banked and archsetup has nothing to
add there. What it can do is let a machine that would rather have the latency
say so, and say what that costs -- in the task description, not in a note
nobody reads.

Mechanism, and why this one. 802.11 power save has no sysfs attribute:
/sys/class/net/<iface>/power/ carries PCI runtime PM and nothing else, so the
ATTR{} assignment that 81-ethernet-pm.rules uses does not exist here and the
only channel is nl80211, i.e. iw -- which is why iw is a dependency of the
feature rather than a diagnostic.

The alternative considered was iwlmvm's own power_scheme parameter (1-active,
2-balanced, 3-low power, default 2) through modprobe.d. It is tidier, needs no
package, and covers the interface from the moment the module loads. It was not
taken because it only takes effect on the *next* module load, so the task
could not read the machine back and report what is true now -- and "written is
not in force" is the lesson this repo keeps relearning. The setting is applied
directly here and the rule exists for the next boot.

What is NOT measured: that the rule's RUN fires at boot (only that it matches
the device and that iw sets the state now), and whether the setting survives a
reassociation. cfg80211 keeps power save per-wdev rather than per-connection,
which argues it does, but that is reasoning and not a measurement.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import i18n, pacman, sysedit
from .pacman import run
from .prompt import ask_yes

t = i18n.t

NET_DEVICES = Path("/sys/class/net")

# The driver, not the interface name: wlan0/wlp3s0 is a naming policy and the
# driver is what owns power save. Same reasoning as ethernet_pm matching on
# vendor/device rather than on a PCI address -- that address moved once and
# took a measurement rig with it.
DRIVER = "iwlwifi"
PACKAGE = "iw"
IW = "/usr/bin/iw"

UDEV_RULES = Path("/etc/udev/rules.d/83-wifi-power-save.rules")

# RUN+= rather than ATTR{}= because there is no attribute to assign; see the
# module docstring. The absolute path is required -- udev does not carry a
# PATH -- and is where pacman puts iw.
UDEV_CONTENT = f"""\
# Written by archsetup.
# 802.11 power save trades latency for power: measured on this machine, average
# RTT is 36-45 ms with it on against 7.8 ms with it off (jitter 21-29 ms against
# 1.6 ms), and turning it off costs 0.50 W at idle, ~17 min of battery. Power
# save has no sysfs attribute, so this runs iw. Both measurements and the
# reasoning are in core/wifi_power_save.py.
ACTION=="add", SUBSYSTEM=="net", DRIVERS=="{DRIVER}", RUN+="{IW} dev $name set power_save off"
"""


def interfaces() -> list[str]:
    """Wifi interfaces matched the way the rule matches them.

    If this comes back empty the rule has nothing to fire on either, which is
    the property that makes it worth reading sysfs here instead of parsing
    `iw dev`.
    """
    try:
        entries = sorted(NET_DEVICES.iterdir())
    except OSError:
        return []
    found = []
    for entry in entries:
        driver = entry / "device" / "driver"
        try:
            if driver.resolve().name == DRIVER:
                found.append(entry.name)
        except OSError:
            continue
    return found


def current(iface: str) -> str:
    """"on", "off", or "" when it cannot be read.

    Unprivileged: getting power_save does not need root, only setting it does.
    """
    try:
        out = subprocess.run(
            [PACKAGE, "dev", iface, "get", "power_save"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    for line in out.stdout.splitlines():
        if "Power save:" in line:
            return line.split(":", 1)[1].strip().lower()
    return ""


def _apply(ifaces: list[str], want: str) -> tuple[int, bool]:
    """Set every interface and read each one back. Returns (rc, all_agree)."""
    rc = 0
    for iface in ifaces:
        rc |= run(["sudo", PACKAGE, "dev", iface, "set", "power_save", want])
    agree = True
    for iface in ifaces:
        state = current(iface)
        print(t("wifi_power_save.state", iface=iface, state=state or "?"))
        agree = agree and state == want
    return rc, agree


def turn_off() -> int:
    """Give up 0.50 W for a link that answers five times faster.

    The order is deliberate: the setting is applied first and the rule second.
    The rule only fires on an `add` event, so nothing re-applies it during
    normal operation and there is no window to lose -- but a run that died
    between the two would leave a machine whose next boot disagrees with its
    present state, and this order makes the present state the one that is
    right.
    """
    ifaces = interfaces()
    if not ifaces:
        print(t("wifi_power_save.no_device"))
        return 1

    if UDEV_RULES.exists() and all(current(i) == "off" for i in ifaces):
        print(t("wifi_power_save.already_off"))
        return 0

    print(t("wifi_power_save.price"))
    if not ask_yes(t("wifi_power_save.off_q")):
        print(t("msg.cancelled"))
        return 0

    rc = 0
    if not pacman.is_installed(PACKAGE):
        rc = pacman.install([PACKAGE], [])
        if rc != 0:
            print(t("wifi_power_save.needs_iw", pkg=PACKAGE))
            return rc

    rc_set, applied = _apply(ifaces, "off")
    rc |= rc_set

    rule_rc, changed = sysedit.write_with_backup(UDEV_RULES, UDEV_CONTENT)
    rc |= rule_rc
    if changed:
        rc |= run(["sudo", "udevadm", "control", "--reload-rules"])

    if not applied:
        print(t("wifi_power_save.not_applied"))
        return rc | 1

    print(t("wifi_power_save.off_done", path=UDEV_RULES))
    return rc


def turn_on() -> int:
    """Back to the kernel default, which is also the cheaper one.

    This direction does not ask. It restores what the machine shipped with and
    hands back 0.50 W; the cost is latency, and a user who wanted the latency
    is the one running the other task.
    """
    ifaces = interfaces()
    if not ifaces:
        print(t("wifi_power_save.no_device"))
        return 1

    rule_present = UDEV_RULES.exists()
    if not rule_present and all(current(i) == "on" for i in ifaces):
        print(t("wifi_power_save.already_on"))
        return 0

    rc = 0
    if rule_present:
        rc |= run(["sudo", "rm", "-f", str(UDEV_RULES)])
        rc |= run(["sudo", "udevadm", "control", "--reload-rules"])

    rc_set, applied = _apply(ifaces, "on")
    rc |= rc_set

    if not applied:
        print(t("wifi_power_save.not_restored"))
        return rc | 1

    print(t("wifi_power_save.on_done"))
    return rc
