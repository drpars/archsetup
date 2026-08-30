"""A static address on a machine archsetup left on DHCP, and the way back.

Why a task and not a setting: config.toml holds what archsetup itself does,
and an address belongs to the machine's network stack. Same line the rest of
the tool draws.

The shape is forced by three measurements, none of them obvious.

**The wildcard.** The installer writes two .network files that match by
interface *class* -- ``Name=en*`` and ``Name=wl*`` (installer/chroot.py). An
address is per-interface by nature, so a static config cannot be a drop-in
into those: ``20-wired.network.d/`` would put one address on every ethernet
port the machine has. It has to be its own file; and because systemd applies
"the first (in alphanumeric order) of the network files that matches a given
interface ... all later files are ignored, even if they match as well"
(systemd.network(5)), that file both wins outright and inherits nothing. So
it restates what the wildcard file was providing -- RequiredForOnline= and
the route metric -- or those go missing with nothing anywhere reporting it.
The metric is not decoration: chroot.py sets 100 on wired and 600 on wireless
so wired stays ahead of wireless when both links are up, and a static
interface that forgets it still gets an address, still routes, and quietly
stops preferring the cable.

**The gateway is not in the field called Gateways.** ``networkctl
--json=short status <link>`` reports ``"Gateways": null`` while a default
route is up and the human-readable output prints ``Gateway: 192.168.1.1``.
Measured on systemd 261.2, all four links on the machine this was written on,
working and idle alike -- and re-measured 2026-08-31 on a link this task had
just made static, so the null is the field, not something DHCP does. (Those
first four readings were all DHCP; a reader could have taken the quirk for a
property of the lease.) The gateway comes out of ``Routes[]``, the entry
whose destination prefix length is 0. A reader written against the obvious
field name gets None and writes a static config with an address and no route
-- which presents as "the network is broken", not as "archsetup wrote a bad
file".

**"Automatic static IP" has two readings and only one of them exists.**
Freezing what DHCP currently hands out is fully derivable: address, prefix,
gateway and DNS all read back unprivileged. Choosing a free address is not --
nothing the client receives says where the server's pool starts and ends, and
no DHCP option carries it. So the automatic half is an automatic *proposal*,
never an automatic *choice*, and asking is not politeness: the frozen address
came out of the pool, so the server can hand it to another machine while this
one is off. The warning therefore sits in the prompt, not in a document.

What this does not do, on purpose: apply the file behind the user's back.
Writing is safe; ``networkctl reload`` on the link you are reaching the
machine over is not. Three reloads were measured 2026-08-31 (wlan0, both
directions, systemd 261.2) and none dropped the carrier -- but the address
was identical across all three, so surviving TCP connections came free and
the case that actually threatens a session, a reload that moves the address,
is still unmeasured. core/iwd.py made the same call for the same reason -- "restarting iwd
drops the connection and archsetup may well be running over it" -- and this
is the more dangerous version, because a wrong address does not come back on
its own. Both commands, apply and undo, are printed before anything is
written, and applying is offered as a question whose safe answer is no.

NetworkManager is out of scope in this round and says so rather than
guessing. archsetup enables NM when the package is present and only offers
networkd when it is absent (installer/chroot.py), so both stacks are reachable
in the field -- but nmcli has never been run here (not installed on the
machine this was written on) and an unmeasured nmcli recipe is the one thing
this repo does not write down.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import i18n, services, sysedit
from .pacman import run
from .prompt import ask_yes

t = i18n.t

NET_DEVICES = Path("/sys/class/net")
NETWORK_DIR = Path("/etc/systemd/network")

NETWORKD_UNIT = "systemd-networkd"
NM_UNIT = "NetworkManager"

# 10- so the file sorts ahead of the installer's 20-wired/20-wireless. That
# ordering is the entire mechanism -- first match wins -- so the prefix is
# load-bearing, not cosmetic.
STATIC_PREFIX = "10-static-"

# The metrics installer/chroot.py writes into the wildcard files. Repeated
# here because a static file inherits nothing, and pinned by a test so the two
# copies cannot drift apart without something failing.
WIRED_METRIC = 100
WIRELESS_METRIC = 600

WIRED_PREFIXES = ("en", "eth")
WIRELESS_PREFIXES = ("wl",)


def static_file(iface: str) -> Path:
    return NETWORK_DIR / f"{STATIC_PREFIX}{iface}.network"


def interfaces() -> list[str]:
    """Real network interfaces, from sysfs, with no subprocess at all.

    The discriminator is the ``device`` symlink: it exists for an interface
    backed by hardware and not for the virtual ones. Measured on this machine
    -- ``lo`` and libvirt's ``virbr0`` both lack it, ``enp4s0`` and ``wlan0``
    both have it. That matters beyond tidiness: virbr0 carries 192.168.122.1
    and offering it here would invite a user to pin an address onto libvirt's
    own bridge.

    Reading sysfs rather than asking networkctl is what lets status() run in
    the menu's draw path; see status().
    """
    try:
        entries = sorted(NET_DEVICES.iterdir())
    except OSError:
        return []
    return [entry.name for entry in entries if (entry / "device").exists()]


def metric_for(iface: str) -> int | None:
    """The route metric the wildcard file would have given this interface.

    None for a name matching neither class, which is the honest answer: the
    installer's files would not have matched it either, so there is no metric
    to preserve and the kernel default is left alone.
    """
    if iface.startswith(WIRED_PREFIXES):
        return WIRED_METRIC
    if iface.startswith(WIRELESS_PREFIXES):
        return WIRELESS_METRIC
    return None


def configured() -> list[str]:
    """Interfaces this task has written a static file for.

    Read off the directory rather than by walking the current interfaces and
    testing each one. The difference only shows up in the case that matters:
    a file left behind for an interface that is no longer present -- renamed,
    moved to another slot, or a USB adapter that is out. Intersecting with
    sysfs would hide exactly that file, and it is the one a user would want
    the revert task to offer them.
    """
    try:
        entries = sorted(NETWORK_DIR.glob(f"{STATIC_PREFIX}*.network"))
    except OSError:
        return []
    return [entry.name[len(STATIC_PREFIX) : -len(".network")] for entry in entries]


def status() -> str:
    """One menu line, and it names the half it cannot see.

    No subprocess: the menu calls this while drawing, and the row is a file
    check plus a sysfs walk. That restraint is why the line reports what is
    *configured* rather than what is *in force* -- and the difference is not
    academic. ``ConfigSource`` on the live link would answer "DHCPv4" for
    wlan0 and nothing at all for a cable-less enp4s0, because an interface
    with no lease has no address to carry a source. Measured on this machine
    with the cable out: the interface is plainly on DHCP, its .network file
    says so, and the live reading is silent. A line keyed on the live reading
    would print "unknown" for exactly the machine someone is about to give a
    static address to.

    So the file is the answer, and the line says so out loud rather than
    implying a liveness it did not check.
    """
    ifaces = interfaces()
    if not ifaces:
        return t("net_static.status_no_device")
    static = configured()
    verdict = (
        t("net_static.status_static", ifaces=", ".join(static))
        if static
        else t("net_static.status_dhcp")
    )
    return t("net_static.status_line", verdict=verdict, ifaces=", ".join(ifaces))


@dataclass(frozen=True)
class Lease:
    """The IPv4 configuration a link carries right now."""

    address: str
    prefix: int
    gateway: str
    dns: tuple[str, ...]
    source: str


def read_lease(iface: str) -> Lease | None:
    """Read the live IPv4 configuration off a link.

    Shells out, so it is deliberately unreachable from status(); this runs in
    a task body where the terminal is suspended and a fork is free.

    The gateway is dug out of Routes[] rather than read from the field named
    Gateways, which is null even when a default route is up -- see the module
    docstring. Everything here is unprivileged; the whole read was measured as
    uid 1000.
    """
    try:
        out = subprocess.run(
            ["networkctl", "--json=short", "status", iface],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None

    address = next(
        (
            entry
            for entry in data.get("Addresses") or []
            if entry.get("Family") == 2 and entry.get("AddressString")
        ),
        None,
    )
    if address is None:
        return None

    gateway = ""
    for route in data.get("Routes") or []:
        if (
            route.get("Family") == 2
            and route.get("DestinationPrefixLength", 0) == 0
            and route.get("GatewayString")
        ):
            gateway = str(route["GatewayString"])
            break

    dns = tuple(
        str(entry["AddressString"])
        for entry in data.get("DNS") or []
        if entry.get("Family") == 2 and entry.get("AddressString")
    )
    return Lease(
        address=str(address["AddressString"]),
        prefix=int(address.get("PrefixLength", 0)),
        gateway=gateway,
        dns=dns,
        source=str(address.get("ConfigSource") or "?"),
    )


def validate(address: str, prefix: int, gateway: str, dns: Sequence[str]) -> str:
    """Return a locale key describing the first problem, or "" when usable.

    The gateway/subnet check is the one that earns its place: a mistyped
    gateway is accepted by systemd, produces a config that looks right in
    every file, and leaves a machine with an address it cannot route off.
    """
    # The prefix is checked before the address, because IPv4Network raises the
    # same exception for both and a bad prefix would otherwise be reported as
    # a bad address -- sending the user to correct the field that was right.
    if not 1 <= prefix <= 32:
        return "net_static.bad_prefix"
    try:
        network = ipaddress.IPv4Network(f"{address}/{prefix}", strict=False)
        host = ipaddress.IPv4Address(address)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return "net_static.bad_address"
    if gateway:
        try:
            router = ipaddress.IPv4Address(gateway)
        except (ipaddress.AddressValueError, ValueError):
            return "net_static.bad_gateway"
        if router not in network:
            return "net_static.gateway_off_subnet"
        if router == host:
            return "net_static.gateway_is_self"
    for server in dns:
        try:
            ipaddress.IPv4Address(server)
        except (ipaddress.AddressValueError, ValueError):
            return "net_static.bad_dns"
    return ""


def render(
    iface: str, address: str, prefix: int, gateway: str, dns: Sequence[str]
) -> str:
    """The .network file, with everything the wildcard file gave restated.

    DHCP= is absent rather than set to no, and that absence is load-bearing:
    systemd.network(5) documents the default as "no", so an omitted key is a
    static interface. It is called out here because a later reader is more
    likely to add DHCP=no than to trust a missing line.

    IPv6AcceptRA keeps its metric, so v6 stays autoconfigured while v4 is
    pinned. That is what "static IP" means in the request that produced this
    task, and it keeps the wired-before-wireless ordering true on both
    families instead of only on the one being frozen.
    """
    metric = metric_for(iface)
    lines = [
        "# Written by archsetup.",
        f"# Static IPv4 address for {iface}.",
        "#",
        "# This file sorts ahead of the installer's 20-wired/20-wireless, and",
        "# systemd applies only the FIRST matching .network file -- later ones",
        "# are ignored entirely. So RequiredForOnline and the route metric are",
        "# restated below rather than inherited; dropping them would still",
        "# produce a working address and would silently stop preferring the",
        "# cable over wifi.",
        "#",
        "# DHCP= is deliberately absent: systemd.network(5) defaults it to no.",
        "#",
        "# Back to DHCP: run the archsetup task net-dhcp, or remove this file",
        "# and run: sudo networkctl reload",
        "",
        "[Match]",
        f"Name={iface}",
        "",
        "[Link]",
        "RequiredForOnline=routable",
        "",
        "[Network]",
        f"Address={address}/{prefix}",
    ]
    lines += [f"DNS={server}" for server in dns]
    lines.append("")
    if gateway:
        lines += ["[Route]", f"Gateway={gateway}"]
        if metric is not None:
            lines.append(f"Metric={metric}")
        lines.append("")
    if metric is not None:
        lines += ["[IPv6AcceptRA]", f"RouteMetric={metric}", ""]
    return "\n".join(lines)


def _ask(key: str, default: str) -> str:
    """One free-text answer with a default, in the shape reflector's uses."""
    try:
        reply = input(f"{t(key)} [{default}]: ").strip()
    except EOFError:
        return default
    return reply or default


def _owner_ok() -> bool:
    """Refuse rather than guess when something else owns addressing.

    archsetup enables NetworkManager when the package is there and only
    offers networkd when it is not, so a machine it installed can be on
    either -- and dhcpcd is a third possibility, enabled whenever the package
    is present. Writing a .network file on an NM machine produces a file
    systemd-networkd is not running to read: no error, no effect, and a task
    that reported success.
    """
    if services.is_active(NM_UNIT):
        print(t("net_static.owner_nm"))
        return False
    if not services.is_active(NETWORKD_UNIT):
        print(t("net_static.owner_none", unit=NETWORKD_UNIT))
        return False
    return True


def _pick(ifaces: list[str]) -> str:
    """Numbered list, because there is rarely more than one and never many."""
    if len(ifaces) == 1:
        return ifaces[0]
    print(t("net_static.pick"))
    for number, iface in enumerate(ifaces, start=1):
        marker = (
            t("net_static.pick_static")
            if static_file(iface).is_file()
            else t("net_static.pick_dhcp")
        )
        print(f"  {number}) {iface}  {marker}")
    try:
        reply = input(f"{t('net_static.pick_q')} [1]: ").strip() or "1"
    except EOFError:
        return ""
    if not reply.isdigit() or not 1 <= int(reply) <= len(ifaces):
        print(t("net_static.pick_bad"))
        return ""
    return ifaces[int(reply) - 1]


def _offer_reload(iface: str) -> int:
    """Applying is a question, and the safe answer is no.

    `networkctl reload` re-reads the files and reconfigures the link. It does
    not drop the carrier: measured 2026-08-31, three reloads on wlan0 in both
    directions, no `Lost carrier` and the link stayed routable throughout.
    That is less than it sounds. The address never changed in any of the
    three, so nothing was asked of the connections riding on it, and the
    link in question may still be the one carrying this session -- so the
    machine is never reconfigured without being asked, and the command is
    printed either way so a no is not a dead end.
    """
    print(t("net_static.reload_hint"))
    if not services.is_active(NETWORKD_UNIT):
        # Nothing to reload. Offering it anyway would run a command that
        # cannot do the thing the question promises.
        print(t("net_static.reload_no_networkd", unit=NETWORKD_UNIT))
        return 0
    if not ask_yes(t("net_static.reload_q", iface=iface)):
        print(t("net_static.reload_skipped"))
        return 0
    return run(["sudo", "networkctl", "reload"])


def configure() -> int:
    """Freeze what DHCP is handing out, after showing it and asking."""
    if not _owner_ok():
        return 1

    ifaces = interfaces()
    if not ifaces:
        print(t("net_static.no_device"))
        return 1

    iface = _pick(ifaces)
    if not iface:
        print(t("msg.cancelled"))
        return 0

    path = static_file(iface)
    if path.is_file():
        print(t("net_static.exists", path=path))

    lease = read_lease(iface)
    if lease is None:
        # No address to freeze -- a cable-less port, or a link networkd is
        # not configuring. Proposing nothing is better than proposing zeros,
        # so the defaults are empty and every field has to be typed.
        print(t("net_static.no_lease", iface=iface))
        address, prefix_text, gateway, dns_text = "", "24", "", ""
    else:
        print(
            t(
                "net_static.lease",
                iface=iface,
                address=lease.address,
                prefix=lease.prefix,
                gateway=lease.gateway or "-",
                dns=", ".join(lease.dns) or "-",
                source=lease.source,
            )
        )
        address = lease.address
        prefix_text = str(lease.prefix)
        gateway = lease.gateway
        dns_text = ", ".join(lease.dns)

    # The warning goes here, immediately above the questions, because this is
    # the decision it applies to. The address being proposed came out of the
    # server's pool and nothing the client receives says where that pool ends.
    print(t("net_static.pool_warning"))

    address = _ask("net_static.ask_address", address)
    prefix_text = _ask("net_static.ask_prefix", prefix_text)
    gateway = _ask("net_static.ask_gateway", gateway)
    dns_text = _ask("net_static.ask_dns", dns_text)

    if not prefix_text.isdigit():
        print(t("net_static.bad_prefix"))
        return 1
    prefix = int(prefix_text)
    dns = [server.strip() for server in dns_text.split(",") if server.strip()]

    problem = validate(address, prefix, gateway, dns)
    if problem:
        print(t(problem))
        return 1

    content = render(iface, address, prefix, gateway, dns)
    print(t("net_static.plan", path=path))
    print(content)
    # Both ways out are on screen before the write, not after it: a machine
    # that loses its address to a typo cannot be told anything afterwards.
    print(t("net_static.undo_hint", path=path))
    if not ask_yes(t("net_static.write_q", path=path)):
        print(t("msg.cancelled"))
        return 0

    # The sibling .bak write_with_backup leaves is inert in this directory:
    # systemd.network(5) reads only files ending in .network and ignores every
    # other extension, so a .network.bak is not a second, older config. That
    # is the hazard the backup parameter exists for, and it does not apply.
    rc, _ = sysedit.write_with_backup(path, content)
    if rc != 0:
        return rc

    print(t("net_static.written", path=path, iface=iface))
    return rc | _offer_reload(iface)


def revert() -> int:
    """Remove the static file and hand the interface back to the wildcard.

    Deliberately not gated on _owner_ok(), unlike configure(). That gate
    exists because *writing* a .network file for a stack that is not running
    is a silent no-op; removing one never is. Gating the escape hatch on a
    check that has nothing to do with escaping would refuse the recovery path
    on precisely the machine whose network someone has just changed -- and a
    file archsetup wrote is archsetup's to take back whoever owns addressing
    now. Only the reload offer asks about networkd, because only the reload
    needs it.
    """
    static = configured()
    if not static:
        print(t("net_static.revert_none"))
        return 0

    iface = _pick(static)
    if not iface:
        print(t("msg.cancelled"))
        return 0

    path = static_file(iface)
    print(t("net_static.revert_plan", path=path, iface=iface))
    if not ask_yes(t("net_static.revert_q", iface=iface)):
        print(t("msg.cancelled"))
        return 0

    rc = run(["sudo", "rm", "-f", str(path)])
    if rc != 0:
        return rc

    # Removing the file is not the same as being back on DHCP: the link keeps
    # the address it was configured with until networkd re-reads the files.
    # "Written is not in force" runs in both directions.
    print(t("net_static.reverted", path=path, iface=iface))
    return rc | _offer_reload(iface)
