"""Scanning setup: the two parsers, the file it writes, and the gate.

The fixtures below reproduce output *shapes* measured on 2026-08-27, with the
addresses and names replaced by documentation values -- a real address here
would be personal data in a public repo, and correct on one network only.
"""

from pathlib import Path

import pytest

from archsetup.core import i18n, pacman, prompt, scanning, services

# `avahi-browse -prt`, as measured -- against a control service, because the
# scanner was powered down: resolved rows start with `=`, a device announces
# once per protocol, and the IPv6 row carries a link-local address. The field
# order belongs to avahi-browse, not to the service type.
BROWSE = """\
+;wlan0;IPv6;EPSON-EXAMPLE;_scanner._tcp;local
+;wlan0;IPv4;EPSON-EXAMPLE;_scanner._tcp;local
=;wlan0;IPv6;EPSON-EXAMPLE;_scanner._tcp;local;scanner.local;fe80::1;1865;"txtvers=1" "scannerAvailable=1"
=;wlan0;IPv4;EPSON-EXAMPLE;_scanner._tcp;local;scanner.local;192.0.2.14;1865;"txtvers=1" "scannerAvailable=1"
"""

# The other measured shape, 2026-08-28, off a real `_scanner._tcp` row this
# time: the same device, but its IPv6 row carries the IPv4 address too, and
# the name arrives with avahi's decimal byte escapes in it.
BROWSE_REAL = """\
=;wlan0;IPv6;EPSON\\032L3250\\032Series;_scanner._tcp;local;scanner.local;192.0.2.14;1865;"txtvers=1" "scannerAvailable=1"
=;wlan0;IPv4;EPSON\\032L3250\\032Series;_scanner._tcp;local;scanner.local;192.0.2.14;1865;"txtvers=1" "scannerAvailable=1"
"""

# `scanimage -L`, as measured against the non-free backend.
NONFREE_LISTING = (
    "device `epsonscan2:networkscanner:esci2:network:192.0.2.14' "
    "is a EPSON network scanner flatbed scanner\n"
)
# Any device that is not the non-free backend. Only the prefix is load
# bearing here; the rest of a repo backend's device string is not parsed.
FREE_LISTING = "device `epsonds:libusb:001:004' is a Epson Example flatbed scanner\n"

NOTHING = (
    "No scanners were identified. If you were expecting something different,\n"
    "check that the scanner is plugged in, turned on and detected by the\n"
    "sane-find-scanner tool (if appropriate).\n"
)


# --------------------------------------------------------------------------
# announcements
# --------------------------------------------------------------------------


def test_the_protocol_rows_collapse_and_the_link_local_one_loses():
    """The correction that made this parser worth writing.

    The same scanner announces over both protocols and the IPv6 row holds a
    link-local address, which the config file has nowhere to put a scope id
    for. One entry has to come out of the two rows, and it has to be the
    usable address rather than whichever row avahi printed first.
    """
    assert scanning.announcements_in(BROWSE) == [
        scanning.Announcement("EPSON-EXAMPLE", "scanner.local", "192.0.2.14", "1865")
    ]


def test_the_other_measured_shape_also_yields_one_entry():
    """On the real scanner both rows carried the same IPv4 address.

    An IPv4-only filter would be right here by accident; merging is right for
    both shapes that have actually been seen.
    """
    assert scanning.announcements_in(BROWSE_REAL) == [
        scanning.Announcement(
            "EPSON L3250 Series", "scanner.local", "192.0.2.14", "1865"
        )
    ]


def test_the_hostname_is_carried_out_of_the_row_it_was_already_in():
    """field 6, which the parser used to skip past on its way to the address."""
    assert scanning.announcements_in(BROWSE)[0].hostname == "scanner.local"


def test_unresolved_rows_are_not_mistaken_for_devices():
    """`+` rows carry no address at all; only `=` rows are resolved."""
    plus_only = "\n".join(BROWSE.splitlines()[:2]) + "\n"
    assert scanning.announcements_in(plus_only) == []


def test_a_shifted_row_yields_nothing_rather_than_nonsense():
    """A `;` inside the name field moves every index after it.

    The address cannot be the guard any more -- it is allowed to be a host
    name, so the shifted `scanner.local` would parse perfectly and the port
    would quietly become the address. The anchor is the service type in field
    4, which no shift survives.
    """
    shifted = (
        "=;wlan0;IPv4;EPSON;EXAMPLE;_scanner._tcp;local;scanner.local;"
        "192.0.2.14;1865;\"txtvers=1\"\n"
    )
    assert scanning.announcements_in(shifted) == []


def test_a_row_for_another_service_is_not_read_as_a_scanner():
    """The same anchor, doing its other job."""
    other = (
        "=;wlan0;IPv4;EXAMPLE;_ipp._tcp;local;printer.local;192.0.2.15;631;"
        '"txtvers=1"\n'
    )
    assert scanning.announcements_in(other) == []


def test_a_non_numeric_port_is_dropped():
    """The second half of the shift anchor."""
    broken = (
        "=;wlan0;IPv4;EXAMPLE;_scanner._tcp;local;scanner.local;192.0.2.14;"
        'nope;"txtvers=1"\n'
    )
    assert scanning.announcements_in(broken) == []


def test_empty_output_is_an_empty_list_not_an_error():
    """Measured: with nothing announcing, avahi-browse prints nothing, rc=0."""
    assert scanning.announcements_in("") == []


def test_the_escaped_name_is_decoded_for_display():
    """avahi escapes a byte as a backslash and three DECIMAL digits.

    Two measured names agree on the base: 032 for a space, and 196/177 for
    the two UTF-8 bytes of "\u0131". Octal would have been 040 and 304/261.
    """
    assert scanning.unescape_name("EPSON\\032L3250\\032Series") == "EPSON L3250 Series"
    assert scanning.unescape_name("Yaz\\196\\177c\\196\\177") == "Yaz\u0131c\u0131"


def test_a_name_the_rule_does_not_cover_is_left_exactly_as_printed():
    """Decoding is display-only, so a wrong guess must cost nothing."""
    assert scanning.unescape_name("plain-name") == "plain-name"
    # 196 with no second byte is not UTF-8; the raw string survives instead.
    assert scanning.unescape_name("half\\196") == "half\\196"


# --------------------------------------------------------------------------
# what may stand in for an address
# --------------------------------------------------------------------------


def test_both_shapes_are_addresses_and_junk_is_not():
    assert scanning.is_address("192.0.2.14")
    assert scanning.is_address("scanner.local")
    assert not scanning.is_address("")
    assert not scanning.is_address("no spaces allowed")


def test_a_mistyped_address_stays_an_error_instead_of_becoming_a_host():
    """The reason digits-and-dots is refused as a name.

    Accepting any syntactically valid host name would quietly turn a typo
    into a host that never answers, which is the failure mode this whole
    module is about.
    """
    assert not scanning.is_hostname("192.168.1.2555")
    assert not scanning.is_address("192.168.1.2555")


def test_the_name_wins_when_the_machine_can_resolve_it():
    entry = scanning.Announcement("EPSON", "scanner.local", "192.0.2.14", "1865")
    assert scanning.address_for(entry, mdns_ready=True) == "scanner.local"


def test_the_address_is_written_when_local_names_do_not_resolve():
    """A name that does not resolve would list a device and then hang."""
    entry = scanning.Announcement("EPSON", "scanner.local", "192.0.2.14", "1865")
    assert scanning.address_for(entry, mdns_ready=False) == "192.0.2.14"


def test_a_link_local_only_row_yields_nothing_to_write():
    entry = scanning.Announcement("EPSON", "", "fe80::1", "1865")
    assert scanning.address_for(entry, mdns_ready=True) == ""
    assert scanning.address_for(entry, mdns_ready=False) == ""


# --------------------------------------------------------------------------
# the device listing
# --------------------------------------------------------------------------


def test_only_the_quoted_device_name_is_parsed():
    assert scanning.devices_in(NONFREE_LISTING) == [
        "epsonscan2:networkscanner:esci2:network:192.0.2.14"
    ]


def test_the_no_scanners_message_parses_as_no_devices():
    assert scanning.devices_in(NOTHING) == []


# --------------------------------------------------------------------------
# the user file
# --------------------------------------------------------------------------


def test_the_file_is_a_header_and_one_address():
    assert scanning.conf_content("192.0.2.14") == "[Network]\n192.0.2.14\n"


def test_the_address_is_read_back_past_the_header_and_comments(tmp_path, monkeypatch):
    """The header is not an address, and `#` disables a line (Epson's 8.1.2)."""
    conf = tmp_path / "epsonscan2.conf"
    conf.write_text("[Network]\n# 192.0.2.99\n192.0.2.14\n", encoding="utf-8")
    monkeypatch.setattr(scanning, "NETWORK_CONF", conf)
    assert scanning.configured_address() == "192.0.2.14"


def test_a_missing_file_reads_as_no_address(tmp_path, monkeypatch):
    monkeypatch.setattr(scanning, "NETWORK_CONF", tmp_path / "absent.conf")
    assert scanning.configured_address() == ""


def test_a_working_name_config_reads_as_configured(tmp_path, monkeypatch):
    """The bug this handover was opened for.

    An IPv4-only reading called a `<host>.local` file unconfigured -- on the
    machine it was found on, that same file had just produced a scan at rc 0.
    """
    conf = tmp_path / "epsonscan2.conf"
    conf.write_text("[Network]\nscanner.local\n", encoding="utf-8")
    monkeypatch.setattr(scanning, "NETWORK_CONF", conf)
    assert scanning.configured_address() == "scanner.local"


def test_a_file_holding_something_that_is_not_an_address_reads_as_none(
    tmp_path, monkeypatch
):
    """"Defined" over an unusable value is worse than saying nothing."""
    conf = tmp_path / "epsonscan2.conf"
    conf.write_text("[Network]\n192.0.2.999\n", encoding="utf-8")
    monkeypatch.setattr(scanning, "NETWORK_CONF", conf)
    assert scanning.configured_address() == ""


# --------------------------------------------------------------------------
# the menu row
# --------------------------------------------------------------------------


def test_the_row_names_what_is_missing_on_a_bare_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(scanning, "SANE_DLL", tmp_path / "no-dll")
    monkeypatch.setattr(scanning, "NONFREE_BACKEND", tmp_path / "no-backend")
    monkeypatch.setattr(scanning, "NETWORK_CONF", tmp_path / "no-conf")
    row = scanning.status()
    assert i18n.t("scanning.status_sane_no") in row
    assert i18n.t("scanning.status_driver_no") in row
    assert i18n.t("scanning.status_net_no") in row


def test_the_row_reports_the_configured_address(tmp_path, monkeypatch):
    dll = tmp_path / "dll.conf"
    dll.write_text("epson2\n", encoding="utf-8")
    backend = tmp_path / "libsane-epsonscan2.so"
    backend.write_text("", encoding="utf-8")
    conf = tmp_path / "epsonscan2.conf"
    conf.write_text("[Network]\n192.0.2.14\n", encoding="utf-8")
    monkeypatch.setattr(scanning, "SANE_DLL", dll)
    monkeypatch.setattr(scanning, "NONFREE_BACKEND", backend)
    monkeypatch.setattr(scanning, "NETWORK_CONF", conf)
    row = scanning.status()
    assert "192.0.2.14" in row
    assert i18n.t("scanning.status_driver_yes") in row


def test_the_row_touches_no_subprocess(monkeypatch, tmp_path):
    """The row is computed while the menu draws, where a subprocess is banned.

    scanimage -L was measured at 7.3 s here, which is what that ban is for.
    """

    def explode(*args, **kwargs):
        raise AssertionError("status() must not run anything")

    monkeypatch.setattr(scanning.subprocess, "run", explode)
    monkeypatch.setattr(scanning, "SANE_DLL", tmp_path / "no-dll")
    monkeypatch.setattr(scanning, "NONFREE_BACKEND", tmp_path / "no-backend")
    monkeypatch.setattr(scanning, "NETWORK_CONF", tmp_path / "no-conf")
    scanning.status()


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


@pytest.fixture
def sealed_task(monkeypatch, tmp_path):
    """configure() with its package installs, services and queries replaced.

    The parsers underneath still run for real -- only the machine is replaced,
    which is the same split conftest uses for the other network rows.
    """
    installs: list[tuple[list[str], list[str]]] = []

    def fake_install(repo, aur):
        installs.append((list(repo), list(aur)))
        return 0

    monkeypatch.setattr(pacman, "install", fake_install)
    monkeypatch.setattr(scanning.pacman, "install", fake_install)
    monkeypatch.setattr(services, "enable_now", lambda unit: 0)
    monkeypatch.setattr(scanning.services, "enable_now", lambda unit: 0)
    monkeypatch.setattr(scanning, "NONFREE_BACKEND", tmp_path / "no-backend")
    monkeypatch.setattr(scanning, "NETWORK_CONF", tmp_path / "no-conf")
    monkeypatch.setattr(scanning, "SANE_DLL", tmp_path / "no-dll")
    # Pinned rather than inherited: this one reads the live /etc/nsswitch.conf,
    # so leaving it alone would make these tests say different things on the
    # machine they were written on and in CI.
    monkeypatch.setattr(scanning.printing, "mdns_ready", lambda: False)
    return installs


def _listing(monkeypatch, output: str):
    monkeypatch.setattr(scanning, "_capture", lambda cmd, timeout: output)


def _machine(monkeypatch, listing: str, browse: str = ""):
    """Both queries at once, told apart by the command being run."""
    monkeypatch.setattr(
        scanning,
        "_capture",
        lambda cmd, timeout: browse if cmd[0] == scanning.AVAHI_BROWSE else listing,
    )


def test_a_repo_backend_finding_a_scanner_ends_the_task_before_the_aur(
    sealed_task, monkeypatch
):
    """The whole point of the shape: the AUR question is not asked by default.

    One device's elimination is not a general one, so the repo path is tried
    on the machine being set up and the non-free half only opens when it comes
    back empty.
    """
    _listing(monkeypatch, FREE_LISTING)
    monkeypatch.setattr(
        prompt, "ask_yes", lambda q: pytest.fail("nothing should be asked")
    )
    monkeypatch.setattr(
        scanning.prompt, "ask_yes", lambda q: pytest.fail("nothing should be asked")
    )

    assert scanning.configure() == 0
    assert sealed_task == [(list(scanning.REPO_PACKAGES), [])]


def test_a_device_from_the_non_free_backend_does_not_count_as_the_repo_path(
    sealed_task, monkeypatch
):
    """The trap this prefix check exists for.

    epsonscan2 produces its device out of the config file, so on a machine a
    previous run configured, `scanimage -L` answers with a device whether or
    not anything free is installed. Counting it as "the repo found something"
    would report the non-free driver as the free path.
    """
    _listing(monkeypatch, NONFREE_LISTING)
    monkeypatch.setattr(scanning.prompt, "ask_yes", lambda q: False)

    assert scanning.configure() == 0
    # The repo transaction ran; the AUR one did not, because the gate was
    # reached and declined.
    assert sealed_task == [(list(scanning.REPO_PACKAGES), [])]


def test_declining_the_gate_installs_nothing_from_the_aur(sealed_task, monkeypatch):
    _listing(monkeypatch, NOTHING)
    monkeypatch.setattr(scanning.prompt, "ask_yes", lambda q: False)

    assert scanning.configure() == 0
    assert [aur for _, aur in sealed_task if aur] == []


def test_accepting_the_gate_installs_the_aur_package_and_asks_for_an_address(
    sealed_task, monkeypatch, capsys
):
    _listing(monkeypatch, NOTHING)
    monkeypatch.setattr(scanning.prompt, "ask_yes", lambda q: True)
    # Nothing announcing and nothing typed: the task must not invent one.
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    scanning.configure()

    assert [aur for _, aur in sealed_task if aur] == [
        list(scanning.NONFREE_AUR_PACKAGES)
    ]
    assert not scanning.NETWORK_CONF.exists()
    assert i18n.t("scanning.not_defined", path=scanning.NETWORK_CONF,
                  header=scanning.NETWORK_HEADER) in capsys.readouterr().out


def test_a_failed_avahi_start_does_not_abandon_the_config_step(
    sealed_task, monkeypatch, tmp_path
):
    """A non-zero exit is right; stopping half way through is not.

    avahi is enabled for discovery. If enabling it fails and that failure is
    read as the install's, the task returns with the package on disk and no
    config file -- and `scanimage -L` then answers with nothing at all, which
    reads exactly like the driver not working.
    """
    monkeypatch.setattr(scanning.services, "enable_now", lambda unit: 1)
    _listing(monkeypatch, NOTHING)
    monkeypatch.setattr(scanning.prompt, "ask_yes", lambda q: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "192.0.2.14")

    rc = scanning.configure()
    assert rc != 0
    assert scanning.NETWORK_CONF.read_text(encoding="utf-8") == (
        "[Network]\n192.0.2.14\n"
    )


def test_an_installed_driver_does_not_reopen_the_non_free_question(
    sealed_task, monkeypatch, tmp_path
):
    backend = tmp_path / "libsane-epsonscan2.so"
    backend.write_text("", encoding="utf-8")
    monkeypatch.setattr(scanning, "NONFREE_BACKEND", backend)
    _listing(monkeypatch, NONFREE_LISTING)
    monkeypatch.setattr(scanning.prompt, "ask_yes",
                        lambda q: pytest.fail("already answered once"))
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    scanning.configure()
    assert [aur for _, aur in sealed_task if aur] == []


def test_choosing_an_announced_scanner_writes_its_name_not_its_address(
    sealed_task, monkeypatch, capsys
):
    """The durable half of the handover.

    The name comes from the MAC, so the config survives a new DHCP lease --
    which is what printing already had and scanning did not.
    """
    monkeypatch.setattr(scanning.printing, "mdns_ready", lambda: True)
    _machine(monkeypatch, NOTHING, BROWSE)
    monkeypatch.setattr(scanning.prompt, "ask_yes", lambda q: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    scanning.configure()

    assert scanning.NETWORK_CONF.read_text(encoding="utf-8") == (
        "[Network]\nscanner.local\n"
    )
    assert i18n.t(
        "scanning.name_preferred", name="scanner.local", address="192.0.2.14"
    ) in capsys.readouterr().out


def test_without_a_resolver_the_address_is_written_and_the_reason_is_said(
    sealed_task, monkeypatch, capsys
):
    """Writing a name that does not resolve would rebuild this module's trap.

    The listing would still show the device and the scan would still hang, so
    the stale-able address is the safer of the two and the trade is printed
    rather than left for the user to discover.
    """
    _machine(monkeypatch, NOTHING, BROWSE)
    monkeypatch.setattr(scanning.prompt, "ask_yes", lambda q: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    scanning.configure()

    assert scanning.NETWORK_CONF.read_text(encoding="utf-8") == (
        "[Network]\n192.0.2.14\n"
    )
    assert i18n.t("scanning.name_unresolvable", name="scanner.local",
                  address="192.0.2.14",
                  path=scanning.printing.NSSWITCH) in capsys.readouterr().out


def test_a_typed_name_is_written_and_not_refused(sealed_task, monkeypatch, capsys):
    """The third place the old IPv4 check bit: it rejected a working name.

    With no resolver the risk is named, but the value is the user's choice
    and it is written.
    """
    _machine(monkeypatch, NOTHING, "")
    monkeypatch.setattr(scanning.prompt, "ask_yes", lambda q: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "scanner.local")

    scanning.configure()

    assert scanning.NETWORK_CONF.read_text(encoding="utf-8") == (
        "[Network]\nscanner.local\n"
    )
    assert i18n.t("scanning.typed_name_unresolved", name="scanner.local",
                  path=scanning.printing.NSSWITCH) in capsys.readouterr().out


def test_a_typed_value_that_is_neither_shape_still_writes_nothing(
    sealed_task, monkeypatch, capsys
):
    _machine(monkeypatch, NOTHING, "")
    monkeypatch.setattr(scanning.prompt, "ask_yes", lambda q: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "192.0.2.999")

    scanning.configure()

    assert not scanning.NETWORK_CONF.exists()
    assert i18n.t("scanning.bad_address", value="192.0.2.999") in capsys.readouterr().out


def test_the_listing_is_never_reported_as_proof_of_a_reachable_scanner(
    sealed_task, monkeypatch, tmp_path, capsys
):
    """Measured in both directions: the listing follows the file, not the wire.

    A later session quotes the summary line, not the raw output, so the
    caveat has to be printed next to the listing rather than left implied.
    """
    backend = tmp_path / "libsane-epsonscan2.so"
    backend.write_text("", encoding="utf-8")
    conf = tmp_path / "epsonscan2.conf"
    conf.write_text("[Network]\n192.0.2.14\n", encoding="utf-8")
    monkeypatch.setattr(scanning, "NONFREE_BACKEND", backend)
    monkeypatch.setattr(scanning, "NETWORK_CONF", conf)
    _listing(monkeypatch, NONFREE_LISTING)
    monkeypatch.setattr(scanning.prompt, "ask_yes", lambda q: False)

    scanning.configure()
    assert i18n.t("scanning.listed_caveat") in capsys.readouterr().out


# --------------------------------------------------------------------------
# the strings themselves
# --------------------------------------------------------------------------


def test_every_string_this_module_asks_for_exists():
    """A missing key returns the key, silently, in the middle of a sentence."""
    import re

    source = Path(scanning.__file__).read_text(encoding="utf-8")
    keys = set(re.findall(r't\(\s*"(scanning\.[a-z_]+)"', source))
    assert keys, "no strings found -- the search stopped matching"
    missing = sorted(key for key in keys if i18n.t(key) == key)
    assert missing == []
