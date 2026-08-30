"""Static addressing: the file it writes, the trap it reads around, the gate.

The JSON fixture reproduces the *shape* measured on 2026-08-31 against
systemd 261.2, with the addresses replaced by documentation values (RFC 5737)
-- a real address here would be correct on one network only. The one field
worth reading twice is ``"Gateways": null``: that is not a trimmed fixture,
it is what the live command returns while a default route is up.
"""

import json

import pytest

from archsetup.core import i18n, net_static, services
from archsetup.installer import chroot

# `networkctl --json=short status wlan0`, shape as measured. Gateways is null
# and the default route lives in Routes[]; a "Destination" key is absent on a
# default route, which is why the reader keys on DestinationPrefixLength.
LEASE_JSON = json.dumps(
    {
        "Index": 4,
        "Name": "wlan0",
        "NetworkFile": "/etc/systemd/network/20-wireless.network",
        "Gateways": None,
        "Addresses": [
            {
                "Family": 10,
                "AddressString": "fe80::1",
                "PrefixLength": 64,
                "ConfigSource": "foreign",
            },
            {
                "Family": 2,
                "AddressString": "192.0.2.82",
                "PrefixLength": 24,
                "ConfigSource": "DHCPv4",
            },
        ],
        "Routes": [
            {"Family": 2, "DestinationPrefixLength": 24, "ConfigSource": "DHCPv4"},
            {
                "Family": 2,
                "DestinationPrefixLength": 0,
                "GatewayString": "192.0.2.1",
                "ConfigSource": "DHCPv4",
                "Priority": 600,
            },
        ],
        "DNS": [
            {"Family": 2, "AddressString": "192.0.2.1", "ConfigSource": "DHCPv4"}
        ],
    }
)


class _Completed:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_the_gateway_is_read_from_routes_not_from_the_field_called_gateways(
    monkeypatch,
):
    """The measured trap, pinned.

    `networkctl --json=short` reports Gateways as null while the text output
    prints a gateway, so a reader written against the obvious field name
    produces a config with an address and no default route -- a machine that
    is up, addressed, and cannot reach anything. Measured across all four
    links on the machine this was written on, idle and working alike.
    """
    monkeypatch.setattr(
        net_static.subprocess, "run", lambda *a, **k: _Completed(LEASE_JSON)
    )
    lease = net_static.read_lease("wlan0")
    assert lease is not None
    assert lease.gateway == "192.0.2.1"
    assert (lease.address, lease.prefix) == ("192.0.2.82", 24)
    assert lease.dns == ("192.0.2.1",)
    # The source is what tells a static config apart from a frozen lease.
    assert lease.source == "DHCPv4"


def test_a_link_with_no_ipv4_address_proposes_nothing(monkeypatch):
    """A cable-less port has no lease to freeze, and must not fake one.

    Measured on this machine with the cable out: enp4s0 is configured for
    DHCP and carries no IPv4 address at all. Returning a zero-filled Lease
    here would put 0.0.0.0 in front of the user as a proposal.
    """
    empty = json.dumps({"Index": 2, "Name": "enp4s0", "Addresses": [], "Routes": []})
    monkeypatch.setattr(
        net_static.subprocess, "run", lambda *a, **k: _Completed(empty)
    )
    assert net_static.read_lease("enp4s0") is None


def test_the_rendered_file_restates_what_the_wildcard_file_gave():
    """A static file inherits nothing, so it has to say everything.

    systemd applies only the first matching .network file and ignores the
    rest, so RequiredForOnline and the route metric are not inherited from
    20-wireless.network -- they are simply gone unless restated. Losing the
    metric still produces a working address, which is exactly why nothing
    would report it.
    """
    text = net_static.render("wlan0", "192.0.2.82", 24, "192.0.2.1", ["192.0.2.1"])
    assert "[Match]\nName=wlan0" in text
    assert "RequiredForOnline=routable" in text
    assert "Address=192.0.2.82/24" in text
    assert "DNS=192.0.2.1" in text
    assert f"Metric={net_static.WIRELESS_METRIC}" in text
    assert f"RouteMetric={net_static.WIRELESS_METRIC}" in text
    assert "Gateway=192.0.2.1" in text


def test_dhcp_is_absent_rather_than_set_to_no():
    """The absence is load-bearing and easy to "fix" into a bug.

    systemd.network(5) defaults DHCP= to no, so an omitted key is a static
    interface. A later reader adding DHCP=no changes nothing; one adding
    DHCP=yes would hand the interface back to the server while the file still
    claims to be static.
    """
    text = net_static.render("enp4s0", "192.0.2.10", 24, "192.0.2.1", [])
    assert "DHCP=" not in text.replace("# DHCP= is deliberately absent", "")


def test_wired_keeps_its_metric_ahead_of_wireless():
    """The ordering chroot.py engineered survives the static path.

    Same numbers, two files, and nothing links them at runtime -- so they are
    pinned against the installer's own literals here. If someone retunes the
    wildcard files and not this module, a static wired interface silently
    stops being preferred over wifi.
    """
    assert net_static.WIRED_METRIC < net_static.WIRELESS_METRIC
    assert f"RouteMetric={net_static.WIRED_METRIC}" in chroot.WIRED_NETWORK_CONF
    assert f"RouteMetric={net_static.WIRELESS_METRIC}" in chroot.WIRELESS_NETWORK_CONF
    assert net_static.metric_for("enp4s0") == net_static.WIRED_METRIC
    assert net_static.metric_for("eth0") == net_static.WIRED_METRIC
    assert net_static.metric_for("wlan0") == net_static.WIRELESS_METRIC
    # A name matching neither class gets the kernel default rather than a guess.
    assert net_static.metric_for("usb0") is None
    assert "Metric=" not in net_static.render("usb0", "192.0.2.10", 24, "", [])


def test_the_static_file_sorts_ahead_of_the_installer_files():
    """First match wins, so the prefix is the mechanism, not decoration."""
    ours = net_static.static_file("wlan0").name
    assert ours < "20-wired.network"
    assert ours < "20-wireless.network"
    assert ours.endswith(".network")


def test_only_hardware_backed_interfaces_are_offered(tmp_path, monkeypatch):
    """lo and libvirt's bridge must not be candidates.

    Measured on this machine: enp4s0 and wlan0 carry a `device` symlink, lo
    and virbr0 do not. Offering virbr0 would invite pinning an address onto
    libvirt's own bridge, which already carries one.
    """
    root = tmp_path / "net"
    for name, hardware in (
        ("enp4s0", True),
        ("lo", False),
        ("virbr0", False),
        ("wlan0", True),
    ):
        entry = root / name
        entry.mkdir(parents=True)
        if hardware:
            (entry / "device").mkdir()
    monkeypatch.setattr(net_static, "NET_DEVICES", root)
    assert net_static.interfaces() == ["enp4s0", "wlan0"]


def test_a_gateway_outside_the_subnet_is_refused():
    """The typo systemd accepts and the network does not.

    Every file looks right, networkd reports no error, the machine gets its
    address -- and there is no route off the subnet. Cheap to check here and
    expensive to diagnose there.
    """
    assert net_static.validate("192.0.2.82", 24, "192.0.2.1", []) == ""
    assert (
        net_static.validate("192.0.2.82", 24, "198.51.100.1", [])
        == "net_static.gateway_off_subnet"
    )
    assert (
        net_static.validate("192.0.2.82", 24, "192.0.2.82", [])
        == "net_static.gateway_is_self"
    )
    assert net_static.validate("not-an-ip", 24, "", []) == "net_static.bad_address"
    assert net_static.validate("192.0.2.82", 99, "", []) == "net_static.bad_prefix"
    assert (
        net_static.validate("192.0.2.82", 24, "", ["nope"]) == "net_static.bad_dns"
    )
    # An empty gateway is allowed: a machine on a segment with no router is a
    # real configuration, and Gateway= is then simply absent from the file.
    assert net_static.validate("192.0.2.82", 24, "", []) == ""


def test_the_menu_line_reads_files_and_runs_no_subprocess(tmp_path, monkeypatch):
    """The row is drawn, so it may not fork -- and it may not lie either.

    `ConfigSource` would be the honest answer to "what is in force", but it
    needs networkctl and it is silent on a link with no lease. So the line
    reports what is *configured* and says so, and this test holds both halves:
    no subprocess, and the file is what decides.
    """

    def explode(*args, **kwargs):
        raise AssertionError("durum satiri alt surec calistirdi")

    monkeypatch.setattr(net_static.subprocess, "run", explode)

    net_root = tmp_path / "net"
    (net_root / "wlan0" / "device").mkdir(parents=True)
    conf = tmp_path / "networkd"
    conf.mkdir()
    monkeypatch.setattr(net_static, "NET_DEVICES", net_root)
    monkeypatch.setattr(net_static, "NETWORK_DIR", conf)

    assert i18n.t("net_static.status_dhcp") in net_static.status()

    (conf / "10-static-wlan0.network").write_text("[Match]\nName=wlan0\n")
    line = net_static.status()
    assert i18n.t("net_static.status_static", ifaces="wlan0") in line
    assert net_static.configured() == ["wlan0"]


def test_no_interface_at_all_is_its_own_answer(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(net_static, "NET_DEVICES", empty)
    assert net_static.status() == i18n.t("net_static.status_no_device")


def test_networkmanager_is_refused_rather_than_written_over(monkeypatch, capfd):
    """Writing a .network file on an NM machine is a silent no-op.

    archsetup enables NetworkManager when the package is present and only
    offers networkd when it is absent, so both stacks exist in the field.
    Nothing would read the file and nothing would report an error, so the
    task has to say so instead of returning success.
    """
    monkeypatch.setattr(
        services, "is_active", lambda unit: unit == net_static.NM_UNIT
    )
    assert net_static.configure() == 1
    assert i18n.t("net_static.owner_nm") in capfd.readouterr().out


def test_no_manager_running_still_refuses_to_write(monkeypatch, capfd):
    monkeypatch.setattr(services, "is_active", lambda unit: False)
    assert net_static.configure() == 1
    assert net_static.NETWORKD_UNIT in capfd.readouterr().out


def test_the_escape_hatch_is_not_gated_on_the_stack_being_up(
    monkeypatch, tmp_path, capfd
):
    """revert() must work when configure() would refuse.

    The owner gate exists because writing a file nothing reads is a silent
    no-op. Removing one never is -- and gating the way back on it would
    refuse recovery on exactly the machine someone has just changed the
    network of. Here NetworkManager owns the link, which configure() rejects,
    and the leftover file still comes off.
    """
    conf = tmp_path / "networkd"
    conf.mkdir()
    stale = conf / "10-static-wlan0.network"
    stale.write_text("[Match]\nName=wlan0\n")
    monkeypatch.setattr(net_static, "NETWORK_DIR", conf)
    monkeypatch.setattr(
        services, "is_active", lambda unit: unit == net_static.NM_UNIT
    )
    # `from .prompt import ask_yes` binds the name in this module, so that is
    # where it has to be replaced.
    monkeypatch.setattr(net_static, "ask_yes", lambda *_: True)
    removed = []

    def fake_run(cmd):
        removed.append(cmd)
        stale.unlink()
        return 0

    monkeypatch.setattr(net_static, "run", fake_run)
    assert net_static.revert() == 0
    assert removed and str(stale) in removed[0]
    # And the reload is not offered, because there is nothing to reload.
    assert i18n.t("net_static.reload_no_networkd", unit=net_static.NETWORKD_UNIT) in (
        capfd.readouterr().out
    )


def test_a_file_left_behind_for_a_missing_interface_is_still_offered(
    tmp_path, monkeypatch
):
    """The stale-file case, which an interface-first reader would hide.

    A USB adapter that is out, or a NIC that changed name, leaves a static
    file matching nothing. Deriving the list from the directory rather than
    from sysfs is what makes that file removable instead of invisible.
    """
    conf = tmp_path / "networkd"
    conf.mkdir()
    (conf / "10-static-enp9s0.network").write_text("[Match]\nName=enp9s0\n")
    empty = tmp_path / "no-nics"
    empty.mkdir()
    monkeypatch.setattr(net_static, "NETWORK_DIR", conf)
    monkeypatch.setattr(net_static, "NET_DEVICES", empty)
    assert net_static.configured() == ["enp9s0"]


def test_nothing_to_revert_is_not_an_error(monkeypatch, tmp_path, capfd):
    """An empty queue is a no-op, not a failure -- same as ethernet_pm's."""
    monkeypatch.setattr(
        services, "is_active", lambda unit: unit == net_static.NETWORKD_UNIT
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(net_static, "NET_DEVICES", empty)
    assert net_static.revert() == 0
    assert i18n.t("net_static.revert_none") in capfd.readouterr().out


@pytest.mark.parametrize("task_id", ["net-static", "net-dhcp"])
def test_both_rows_are_registered_in_the_network_group(task_id):
    from archsetup.core import tasks

    task = next(entry for entry in tasks.TASKS if entry.id == task_id)
    assert task.group == "network"
    assert task.state is net_static.status
    # The pair shares one reader, the way the power-saving pairs do: the row
    # a user is looking at has to describe the same machine either way.
    assert i18n.t(task.key) != task.key
    assert i18n.t(f"{task.key}_desc") != f"{task.key}_desc"
