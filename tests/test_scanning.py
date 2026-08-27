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


def test_the_ipv4_row_is_taken_and_the_link_local_one_is_not():
    """The correction that made this parser worth writing.

    The same scanner announces over both protocols and the IPv6 row holds a
    link-local address, which the config file has nowhere to put a scope id
    for. Taking the first resolved row would write an unusable address about
    half the time.
    """
    assert scanning.announcements_in(BROWSE) == [
        ("EPSON-EXAMPLE", "192.0.2.14", "1865")
    ]


def test_unresolved_rows_are_not_mistaken_for_devices():
    """`+` rows carry no address at all; only `=` rows are resolved."""
    plus_only = "\n".join(BROWSE.splitlines()[:2]) + "\n"
    assert scanning.announcements_in(plus_only) == []


def test_a_shifted_row_yields_nothing_rather_than_nonsense():
    """A `;` inside the name field moves every index after it.

    avahi's escaping rules were not measured, so the address is validated
    instead of trusted: a row whose eighth field is not an IPv4 address is
    dropped. Without that check this line would define a scanner at "1865".
    """
    shifted = (
        "=;wlan0;IPv4;EPSON;EXAMPLE;_scanner._tcp;local;scanner.local;"
        "192.0.2.14;1865;\"txtvers=1\"\n"
    )
    assert scanning.announcements_in(shifted) == []


def test_empty_output_is_an_empty_list_not_an_error():
    """Measured: with nothing announcing, avahi-browse prints nothing, rc=0."""
    assert scanning.announcements_in("") == []


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


def test_a_file_holding_something_that_is_not_an_address_reads_as_none(
    tmp_path, monkeypatch
):
    """"Defined" over an unusable value is worse than saying nothing."""
    conf = tmp_path / "epsonscan2.conf"
    conf.write_text("[Network]\nscanner.local\n", encoding="utf-8")
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
    return installs


def _listing(monkeypatch, output: str):
    monkeypatch.setattr(scanning, "_capture", lambda cmd, timeout: output)


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
