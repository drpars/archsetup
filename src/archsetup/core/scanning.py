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

**The address is measured, never assumed -- and it need not be an address.**
`avahi-browse -prt _scanner._tcp` is the source. The field order was first
read off a *control* service, because the scanner was powered down that day;
a real `_scanner._tcp` row was finally seen on 2026-08-28 and it corrected
one thing:

    =;wlan0;IPv6;EPSON\\032L3250\\032Series;_scanner._tcp;local;scanner.local;192.0.2.14;1865;...
    =;wlan0;IPv4;EPSON\\032L3250\\032Series;_scanner._tcp;local;scanner.local;192.0.2.14;1865;...

A device does announce once per protocol, but on this one **both rows carry
the same IPv4 address**. The link-local `fe80::` that the old IPv4-only
filter existed for is real -- it was measured, on that control service -- yet
it does not reproduce here, so its reason belongs to another service type
rather than to scanners. Both shapes are handled by merging the protocol rows
instead of filtering them, with an IPv4 address winning over one that is not.

**What goes into the config file is the mDNS name, not the address.** Measured
2026-08-28 on an Epson L3250: with its `<host>.local` name written in place
of the address the device opened (rc 0, real capabilities) and a real scan came back
rc 0 at 310x437 px. The name is derived from the MAC, so a new DHCP lease does
not move it, and it is the same mechanism the printing queue already leans on
(`ipp://<host>.local:631/ipp/print`). Before this the two halves disagreed: a
new lease left printing working and scanning silently broken -- and broken in
the way this module's first paragraphs are about, with `scanimage -L` still
listing the device and the scan hanging.

The name is only preferred when the machine can resolve it, which is
`printing.mdns_ready()` reading the same `hosts:` line the printing task
writes. Writing a name that does not resolve would rebuild that exact trap, so
when mDNS is not set up the address is written instead and the reason is said
out loud.

**What resolves the name was not settled.** None of epsonscan2's libraries
carry `getaddrinfo` or `gethostbyname` among their undefined symbols
(`nm -D`), but they do carry `dlopen`/`dlsym`, so absence proves nothing here.
The gate above is therefore conservative rather than derived: it never writes
a name the ordinary resolver cannot reach, which is right under either
mechanism.

**The config file is the user's, and it is machine- and network-specific**, so
it is written at task time and never shipped: an address in this repo would be
personal data, and correct on exactly one network.

**The gate's premise was finally checked against a reachable device.** On
2026-08-28, with the scanner up and announcing (two resolved `_scanner._tcp`
rows) and `sane-airscan` 0.99.38 installed, `scanimage -L` returned exactly one
device and it was the `epsonscan2:` one; `_uscan._tcp` had no resolved rows at
all. So on this device, on this network, the driverless path really does come
back empty and the AUR question really is what is left -- which is what the
whole shape here was built on and had until then only been reasoned about.

What is NOT measured here: `sane-airscan` has still never been seen *finding*
a scanner, since the only device to hand is the one it cannot talk to, so the
repo path's positive branch stays untested; `simple-scan`'s GUI was installed
but never opened; the USB path (`epsonds` over a cable) was never tried,
because the device that drove all of this sits on Wi-Fi. Nor was a real lease change -- the device's
address could not be moved, so the name's durability rests on the mechanism
rather than on having watched it survive one -- and the name was tried on this
one model only.

One more thing this task does not write: epsonscan2 keeps the address in a
second file as well, `Connection/PreferredInfo.dat`, which its GUI owns and
creates. The file below is the one that makes `scanimage -L` produce the
device, which is what was measured; the GUI was separately seen to rewrite its
own files on exit without normalising the name back to an address.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import i18n, pacman, printing, prompt, services

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

# One label of a host name, RFC 1123 shape: letters, digits, inner hyphens.
# Used to tell a name apart from junk, not to decide whether it resolves.
HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")

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
# what may stand in for the scanner's address
# --------------------------------------------------------------------------


def is_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return True


def is_hostname(value: str) -> bool:
    """Whether the value is a usable host name -- syntax only.

    Not "does it resolve": that is a network question, and this runs while a
    file is being read. What it has to rule out is the two ways a wrong value
    gets in, a shifted avahi field and a typo, so a value made of nothing but
    digits and dots is rejected here and has to pass as a real IPv4 address
    instead. That is what keeps `192.168.1.2555` an error rather than a host
    that will never answer.
    """
    if not value or len(value) > 253:
        return False
    if all(char.isdigit() or char == "." for char in value):
        return False
    return all(HOST_LABEL.match(label) for label in value.rstrip(".").split("."))


def is_address(value: str) -> bool:
    """Either shape the config file accepts."""
    return is_ipv4(value) or is_hostname(value)


def unescape_name(name: str) -> str:
    """A service name as avahi printed it, with its byte escapes resolved.

    avahi's parseable output writes a byte it will not print as a backslash
    and three DECIMAL digits, which two measured names agree on: `\\032` for
    the spaces in `EPSON\\032L3250\\032Series` (32 = space) and `\\196\\177` for
    a UTF-8 `\u0131` (196, 177 = 0xC4, 0xB1). Octal would have written `\\040`
    and `\\304\\261`, so the base is settled by the data rather than assumed.

    Decoding is display-only -- the name is shown so the user can pick their
    device and is never written anywhere -- so anything the rule does not
    cover is left exactly as avahi printed it, including a byte sequence that
    turns out not to be UTF-8.
    """
    out = bytearray()
    index = 0
    while index < len(name):
        digits = name[index + 1 : index + 4]
        if name[index] == "\\" and len(digits) == 3 and digits.isdigit():
            value = int(digits)
            if value < 256:
                out.append(value)
                index += 4
                continue
        out += name[index].encode("utf-8")
        index += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return name


@dataclass(frozen=True)
class Announcement:
    """One scanner as avahi resolved it: what to show, and what to write.

    `name` is decoded for display. `hostname` and `address` are both kept
    because they answer different questions -- which one goes in the file is
    `address_for()`, and the other still helps the user recognise the device.
    """

    name: str
    hostname: str
    address: str
    port: str


def address_for(entry: Announcement, mdns_ready: bool) -> str:
    """Which announced value goes into the config file, or "" for neither.

    The name wins when the machine can resolve it: it comes from the MAC and
    a new DHCP lease does not move it, while the address is exactly the thing
    that goes stale. When mDNS is not set up the name would be a config that
    lists a device and then hangs, so the address is written instead.
    """
    if mdns_ready and is_hostname(entry.hostname):
        return entry.hostname
    return entry.address if is_ipv4(entry.address) else ""


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


def announcements_in(output: str) -> list[Announcement]:
    """One entry per scanner in `avahi-browse -p` output.

    Resolved rows begin with `=` and carry
    `=;iface;proto;name;type;domain;hostname;address;port;txt`, a layout first
    measured on 2026-08-27 against a control service and confirmed on
    2026-08-28 against a real `_scanner._tcp` row.

    **The protocol rows are merged rather than filtered.** A device announces
    once per protocol, and on the control service the IPv6 row held a
    link-local `fe80::` that the config file has nowhere to put a scope id
    for -- but on the real scanner both rows carried the same IPv4 address.
    Keeping only IPv4 rows would therefore be right for one measured shape by
    an accident of the other, so instead the rows are collapsed on the fields
    that identify the device and an IPv4 address displaces one that is not.

    **A `;` in the name is caught by the type field, not by the address.** Such
    a name shifts every index after it, and the address can no longer be the
    guard: it is allowed to be a host name now, and `scanner.local` sitting in
    the shifted address slot would parse perfectly while the port silently
    became an address. The anchor is that field 4 has to be the service that
    was browsed for, which no shift survives, plus a numeric port.
    """
    found: list[Announcement] = []
    at: dict[tuple[str, str, str], int] = {}
    for line in output.splitlines():
        fields = line.split(";")
        if len(fields) < 9 or fields[0] != "=" or fields[4] != SCANNER_SERVICE:
            continue
        port = fields[8]
        if not port.isdigit():
            continue
        entry = Announcement(unescape_name(fields[3]), fields[6], fields[7], port)
        # The device, not the row: what differs between the protocol rows is
        # exactly the field being chosen between.
        key = (entry.name, entry.hostname, entry.port)
        if key not in at:
            at[key] = len(found)
            found.append(entry)
        elif is_ipv4(entry.address) and not is_ipv4(found[at[key]].address):
            found[at[key]] = entry
    return found


def announced_scanners() -> list[Announcement]:
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
    """The whole file: a section header and one address or host name.

    Epson's manual documents one address per line and `#` to disable a line.
    This writes a single entry, so the ordering question never arises. A name
    in that slot was measured to work on 2026-08-28; the file's own syntax
    does not distinguish the two.
    """
    return f"{NETWORK_HEADER}\n{address}\n"


def configured_address() -> str:
    """The address or name the config names, or "" when there is none.

    Blank when the file is missing, has no entry, or holds something that is
    neither shape -- that last part matters because a status row saying
    "defined" over an unusable value is worse than one saying nothing.

    It has to accept a name for the same reason, pointing the other way: an
    IPv4-only reading calls a working `<host>.local` config *unconfigured*,
    which is the worse half of the same mistake -- the machine this was
    corrected on had exactly that file, and a scan through it had just been
    measured at rc 0.
    """
    try:
        text = NETWORK_CONF.read_text(encoding="utf-8")
    except OSError:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(NETWORK_COMMENT) or line == NETWORK_HEADER:
            continue
        if is_address(line):
            return line
    return ""


def _ask_address() -> str:
    """An address chosen from what is announcing, or typed in, or "".

    Discovery first because the handover asked for the address to be measured
    rather than assumed. Typing one in stays available for the case this was
    written in: the scanner was powered down, so nothing announced at all.

    Whichever way the value arrives, the durable choice is only offered when
    the machine can follow it -- and when the user types a name anyway, that
    is their call and it is written, with the risk said rather than refused.
    """
    resolves = printing.mdns_ready()
    scanners = announced_scanners()
    if scanners:
        print(t("scanning.found_header"))
        for index, entry in enumerate(scanners, 1):
            where = entry.address
            if entry.hostname:
                where = f"{entry.hostname} ({entry.address})"
            print(f"  {index}) {entry.name} — {where}:{entry.port}")
        try:
            answer = input(f"{t('scanning.which_q', count=len(scanners))} ").strip()
        except EOFError:
            return ""
        if answer.isdigit() and 1 <= int(answer) <= len(scanners):
            chosen = scanners[int(answer) - 1]
            address = address_for(chosen, resolves)
            if not address:
                print(t("scanning.bad_address",
                        value=chosen.address or chosen.hostname))
                return ""
            if address == chosen.hostname:
                print(t("scanning.name_preferred",
                        name=address, address=chosen.address))
            elif chosen.hostname:
                print(
                    t(
                        "scanning.name_unresolvable",
                        name=chosen.hostname,
                        address=address,
                        path=printing.NSSWITCH,
                    )
                )
            return address
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
    if not is_address(typed):
        print(t("scanning.bad_address", value=typed))
        return ""
    if is_hostname(typed) and not resolves:
        print(t("scanning.typed_name_unresolved", name=typed, path=printing.NSSWITCH))
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
