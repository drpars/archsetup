"""Scanning: the repo path is measured first, and a non-free driver waits behind
a gate.

This task exists because printing's driverless answer does not carry over.
IPP Everywhere's scanning counterpart is eSCL, and a device that prints
driverless may still announce nothing a free backend can talk to -- measured
2026-08-27 on an Epson L3250: no `_uscan._tcp`/`_uscans._tcp`, WSD ports 3702
and 5357 closed, and the repo's `epsonds`, `epson2` and `epson` backends all
dying with `recv: expected = 5, got = 0` against an open TCP 1865. What did
work was `epsonscan2` from the AUR, whose network half is Epson's EULA'd
closed-source plugin.

So the shape here is not "install the non-free driver". It is: install what
the repos offer, **ask the machine** whether that already found a scanner, and
only open the non-free question when it did not. The elimination above is one
device's answer, not a general one -- another scanner may well be driverless,
and `sane-airscan` is in `extra` precisely for that case. The oracle is
`scanimage -L` on the machine being set up rather than anything written here.

**The verification trap, measured in both directions on 2026-08-27.** For this
backend `scanimage -L` answers out of the config file, not off the wire. With
`~/.epsonscan2/Network/epsonscan2.conf` in place and the scanner unreachable --
100% packet loss, ICMP and TCP 1865 both timing out, nothing announcing over
mDNS -- the device was still listed and rc was still 0; with `HOME` pointed at
an empty directory it answered "No scanners were identified", also rc 0. A
listing therefore proves the file, not the device, and this file never reports
it as more than that. (What was measured is unreachability. The device's own
power state was never read, and the two are not the same claim.)

**And the scan that follows does not fail either -- it hangs.** Measured the
same day against that unreachable address: `scanimage -d <device> -o <file>`
wrote nothing to stdout, nothing to stderr beyond net-snmp's own noise, created
no file, and was still running when it was killed at 120 s. So the symptom of
an unreachable scanner is silence rather than an error, which is worth saying
out loud next to the verify command.

The proof of a working scanner is therefore a scan that finishes, and its
criterion is variance rather than file size: an empty flatbed still produces a
valid PNG (grey std 1.2 here against 35.3 with a document on the glass).

**The address is measured, never assumed.** `avahi-browse -prt _scanner._tcp`
is the source, and its `-p` output needed one real correction: a device
announces once per protocol, and the IPv6 row carries a link-local `fe80::`
address that the config file cannot use. In the measured output the IPv6 row
came first, so taking the first resolved row writes an unusable address.

That layout was read off a *control* service rather than off the scanner --
the scanner was powered down that day, and the parseable field order belongs
to avahi-browse rather than to any one service type. What has not been seen,
therefore, is a real `_scanner._tcp` row going through this parser.

**The config file is the user's, and it is machine- and network-specific**, so
it is written at task time and never shipped: an address in this repo would be
personal data, and correct on exactly one network.

What is NOT measured here: `sane-airscan` was never installed on the machine
this was written on, so the repo path has been reasoned about rather than
seen finding a scanner; `simple-scan`'s GUI was installed but never opened;
the USB path (`epsonds` over a cable) was never tried, because the device that
drove all of this sits on Wi-Fi.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from pathlib import Path

from . import i18n, pacman, prompt, services

t = i18n.t

# sane brings scanimage and the backends; sane-airscan is the driverless
# (eSCL/WSD) path, which is what a modern scanner most likely wants and what
# this device measurably does not; simple-scan is a frontend, enumerating
# through the same sane_get_devices() call scanimage -L uses.
REPO_PACKAGES = ("sane", "sane-airscan", "simple-scan")

# Kept as a module constant rather than written inline: --aur-list resolves
# `[*CONSTANT]` out of the task sources, and an AUR name this tool installs
# has to stay visible to that audit.
NONFREE_AUR_PACKAGES = ("epsonscan2",)

# Device strings from the non-free backend start with this. Used to answer one
# question honestly: did the *repo* find something, or is this the driver a
# previous run installed?
NONFREE_PREFIX = "epsonscan2:"

# Discovery here talks to avahi-daemon through avahi-browse. Whether
# sane-airscan needs the same daemon was not measured.
AVAHI_SERVICE = "avahi-daemon.service"

# Owned by `sane` (measured with pacman -Qo), so its presence answers "are the
# backends installed" without a subprocess -- which the menu row needs.
SANE_DLL = Path("/etc/sane.d/dll.conf")

# Shipped by epsonscan2 as a symlink into /usr/lib/epsonscan2/. exists()
# follows it, so this is one check for "the package is installed and its
# backend is where SANE looks".
NONFREE_BACKEND = Path("/usr/lib/sane/libsane-epsonscan2.so")

# Read from the binary rather than guessed, and later confirmed by Epson's own
# manual (8.1.2). The manual is wrong about two things it states: it writes the
# path as /home/.epsonscan2/... and claims root privileges are needed.
NETWORK_CONF = Path.home() / ".epsonscan2" / "Network" / "epsonscan2.conf"
NETWORK_HEADER = "[Network]"
NETWORK_COMMENT = "#"

SCANNER_SERVICE = "_scanner._tcp"
AVAHI_BROWSE = "avahi-browse"
SCANIMAGE = "scanimage"

# `device `epsonscan2:networkscanner:esci2:network:<ip>' is a EPSON network
# scanner flatbed scanner` -- only the quoted device name is parsed, because
# the description after it is the backend's free text.
DEVICE_LINE = re.compile(r"device `([^']*)'")

# Measured 2026-08-27: `scanimage -L` took 7.3 s with one configured device
# that was unreachable. The cap is defensive rather than measured -- backends
# probe the network and a task that hangs with no output is worse than one
# that gives up -- and the browse gets its own because mDNS legitimately waits.
LIST_TIMEOUT = 60
BROWSE_TIMEOUT = 20


# --------------------------------------------------------------------------
# what the machine currently sees
# --------------------------------------------------------------------------


def _capture(cmd: list[str], timeout: int) -> str:
    """stdout of a query, or "" when it cannot answer.

    stderr is dropped rather than shown: `scanimage -L` emitted 36 lines of
    net-snmp "Cannot find module (SNMPv2-MIB)" noise here, which says nothing
    about scanners and reads like a failure. LC_ALL=C because these outputs
    get parsed.
    """
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout


def devices_in(output: str) -> list[str]:
    """SANE device names out of `scanimage -L` output."""
    return DEVICE_LINE.findall(output)


def scan_devices() -> list[str]:
    return devices_in(_capture([SCANIMAGE, "-L"], LIST_TIMEOUT))


def announcements_in(output: str) -> list[tuple[str, str, str]]:
    """(name, address, port) for every IPv4 scanner in `avahi-browse -p` output.

    The field layout was measured on 2026-08-27 against a service that was
    actually on the network, because the scanner was not: resolved rows begin
    with `=` and carry
    `=;iface;proto;name;type;domain;hostname;address;port;txt`.

    Two things this filter is for, both of them real:

    * The same device announces twice, once per protocol, and the IPv6 row
      holds a link-local `fe80::` address. epsonscan2's config file takes a
      bare address with nowhere to put a scope id, so taking the first
      resolved row writes an unusable one about half the time.
    * A `;` inside the name field would shift every index after it. Rather
      than guess at avahi's escaping rules -- which were not measured -- the
      address is parsed as an IPv4 address and a row that fails is dropped, so
      a shifted line yields nothing instead of nonsense.

    Names are passed through exactly as avahi prints them, escapes and all
    (`\\196\\177` for a non-ASCII byte). Decoding them is display-only polish
    and the rules were not measured.
    """
    found: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        fields = line.split(";")
        if len(fields) < 9 or fields[0] != "=" or fields[2] != "IPv4":
            continue
        name, address, port = fields[3], fields[7], fields[8]
        try:
            ipaddress.IPv4Address(address)
        except ValueError:
            continue
        if (name, address, port) not in found:
            found.append((name, address, port))
    return found


def announced_scanners() -> list[tuple[str, str, str]]:
    return announcements_in(
        _capture(
            [AVAHI_BROWSE, "-prt", SCANNER_SERVICE],
            BROWSE_TIMEOUT,
        )
    )


# --------------------------------------------------------------------------
# the network scanner definition (a user file)
# --------------------------------------------------------------------------


def conf_content(address: str) -> str:
    """The whole file: a section header and one address.

    Epson's manual documents one address per line and `#` to disable a line.
    This writes a single entry, so the ordering question never arises.
    """
    return f"{NETWORK_HEADER}\n{address}\n"


def configured_address() -> str:
    """The address the config names, or "" when there is none.

    Blank when the file is missing, has no address line, or holds something
    that is not an IPv4 address -- the last one matters because a status row
    saying "defined" over an unusable value is worse than one saying nothing.
    """
    try:
        text = NETWORK_CONF.read_text(encoding="utf-8")
    except OSError:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(NETWORK_COMMENT) or line == NETWORK_HEADER:
            continue
        try:
            ipaddress.IPv4Address(line)
        except ValueError:
            continue
        return line
    return ""


def _ask_address() -> str:
    """An address chosen from what is announcing, or typed in, or "".

    Discovery first because the handover asked for the address to be measured
    rather than assumed. Typing one in stays available for the case this was
    written in: the scanner was powered down, so nothing announced at all.
    """
    scanners = announced_scanners()
    if scanners:
        print(t("scanning.found_header"))
        for index, (name, address, port) in enumerate(scanners, 1):
            print(f"  {index}) {name} — {address}:{port}")
        try:
            answer = input(f"{t('scanning.which_q', count=len(scanners))} ").strip()
        except EOFError:
            return ""
        if answer.isdigit() and 1 <= int(answer) <= len(scanners):
            return scanners[int(answer) - 1][1]
        if answer:
            print(t("scanning.bad_choice"))
            return ""
    else:
        print(t("scanning.none_announced", service=SCANNER_SERVICE))

    try:
        typed = input(f"{t('scanning.address_q')} ").strip()
    except EOFError:
        return ""
    if not typed:
        return ""
    try:
        ipaddress.IPv4Address(typed)
    except ValueError:
        print(t("scanning.bad_address", value=typed))
        return ""
    return typed


def _define_network_scanner() -> int:
    existing = configured_address()
    if existing:
        print(t("scanning.already_defined", path=NETWORK_CONF, address=existing))
        if not prompt.ask_yes(t("scanning.replace_q")):
            return 0

    address = _ask_address()
    if not address:
        print(t("scanning.not_defined", path=NETWORK_CONF, header=NETWORK_HEADER))
        return 0

    try:
        NETWORK_CONF.parent.mkdir(parents=True, exist_ok=True)
        NETWORK_CONF.write_text(conf_content(address), encoding="utf-8")
    except OSError as error:
        print(t("scanning.write_failed", path=NETWORK_CONF, error=error))
        return 1
    print(t("scanning.defined", path=NETWORK_CONF))
    return 0


# --------------------------------------------------------------------------
# task entry points
# --------------------------------------------------------------------------


def status() -> str:
    """One menu line, built only out of files.

    No subprocess: this row is computed while the menu is being drawn, and
    both of the answers worth having here cost seconds -- `scanimage -L` was
    measured at 7.3 s. What the files can say is what is installed and what is
    configured, which is also all a listing would have proven.
    """
    backends = t(
        "scanning.status_sane_yes" if SANE_DLL.exists() else "scanning.status_sane_no"
    )
    driver = t(
        "scanning.status_driver_yes"
        if NONFREE_BACKEND.exists()
        else "scanning.status_driver_no"
    )
    address = configured_address()
    network = (
        t("scanning.status_net_yes", address=address)
        if address
        else t("scanning.status_net_no")
    )
    return t("scanning.status_line", sane=backends, driver=driver, network=network)


def _report(devices: list[str]) -> None:
    for device in devices:
        print(f"  {device}")


def configure() -> int:
    rc = pacman.install(list(REPO_PACKAGES), [])
    if rc != 0:
        return rc

    # Before looking: this task's own discovery goes through avahi-browse, and
    # a network scanner that is not announcing cannot be found by anything.
    rc |= services.enable_now(AVAHI_SERVICE)

    print(t("scanning.looking"))
    devices = scan_devices()
    free = [name for name in devices if not name.startswith(NONFREE_PREFIX)]
    if free:
        print(t("scanning.free_ok"))
        _report(free)
        print(t("scanning.verify", device=free[0]))
        return rc

    # Only now is the non-free question worth asking, and it is asked once:
    # the backend being on disk means a previous run already answered it.
    if not NONFREE_BACKEND.exists():
        print(t("scanning.no_free_device"))
        print(t("scanning.nonfree_terms", pkg=NONFREE_AUR_PACKAGES[0]))
        if not prompt.ask_yes(t("scanning.nonfree_q")):
            print(t("scanning.declined"))
            return rc
        # The install's own result, not the accumulated one: avahi failing to
        # start is a reason for a non-zero exit and not a reason to stop after
        # the package is already on disk, which is where the config below
        # becomes the difference between a working scanner and a silent one.
        install_rc = pacman.install([], [*NONFREE_AUR_PACKAGES])
        rc |= install_rc
        if install_rc != 0:
            return rc
    else:
        print(t("scanning.driver_present", pkg=NONFREE_AUR_PACKAGES[0]))

    rc |= _define_network_scanner()

    devices = scan_devices()
    if devices:
        print(t("scanning.listed"))
        _report(devices)
        # The whole point of the paragraph in this module's docstring: the
        # line above came out of a file, and saying otherwise here is what a
        # later session would quote.
        print(t("scanning.listed_caveat"))
        print(t("scanning.verify", device=devices[0]))
    else:
        print(t("scanning.still_nothing", path=NETWORK_CONF))
    print(t("scanning.undo", pkg=NONFREE_AUR_PACKAGES[0], path=NETWORK_CONF.parent))
    print(status())
    return rc
