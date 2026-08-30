import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from archsetup.core import (  # noqa: E402
    ethernet_pm,
    i18n,
    mkinitcpio,
    printing,
    scanning,
    secureboot,
    wifi_power_save,
)
from archsetup.installer import blockdev, disk  # noqa: E402


@pytest.fixture(autouse=True)
def turkish_locale():
    i18n.load("tr")


@pytest.fixture
def runlog():
    """Fake pacman.run-style executor that records commands and succeeds."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return 0

    fake_run.calls = calls
    return fake_run


@pytest.fixture(autouse=True)
def sealed_rebuild(monkeypatch, tmp_path, runlog):
    """The two live-machine reaches behind `mkinitcpio.regenerate()`.

    It shells out to `sudo mkinitcpio -P` and then reads Secure Boot state
    from efivarfs. Unsealed, a test that wanders into any rebuild path
    rebuilds the boot images of the machine running the suite, and on an
    enrolled box the check behind it runs `sudo sbctl` -- neither belongs in a
    test, and both answer differently per machine.

    Sealed centrally rather than per fixture on purpose: five call sites reach
    the rebuild, and the four that forgot the verification are exactly the
    ones that would forget this. The command lands in the shared `runlog`, so
    a test already asserting on it sees the -P without arranging anything; a
    test that wants the Secure Boot branch points the constant at its own file.
    """
    monkeypatch.setattr(mkinitcpio, "run", runlog)
    monkeypatch.setattr(secureboot, "SECURE_BOOT_VAR", tmp_path / "no-efivars")


@pytest.fixture(autouse=True)
def sealed_sizes(monkeypatch):
    """The sudo behind `mkinitcpio.sizes()`.

    An image the presets name but that is not on disk sends sizes() to
    `sudo stat`, and in a suite that means an askpass window in front of the
    user -- or, in CI, a wait for one that never comes. Reaching it takes only
    a test whose preset points somewhere it did not create, which is the easy
    thing to write by accident, so the seal is here rather than per file.
    A test that wants the fallback replaces this with its own answer.
    """
    monkeypatch.setattr(mkinitcpio, "_sudo_stat", lambda paths: "")


@pytest.fixture(autouse=True)
def sealed_network_state(monkeypatch, tmp_path):
    """The live-machine reads behind the three network menu rows.

    Those rows carry a computed description, so their state readers run while
    the menu is being *drawn*: `ethernet_pm.status()` walks /sys/bus/pci,
    `wifi_power_save.status()` walks /sys/class/net and then shells out to
    `iw` once per interface, and `printing.status()` reads /etc/nsswitch.conf,
    asks systemd whether cupsd is up and runs fc-match. Any pilot test that
    walks into the network menu would answer differently per machine, and on a
    box with no wifi it would also spawn a subprocess for nothing.

    Sealed by pointing the device roots at empty directories rather than by
    stubbing status(): the readers still run for real, which is the half worth
    testing, and only the hardware underneath them is replaced. Every existing
    test that wants devices already sets these constants itself and still
    wins, because a per-test setattr lands after this one.

    Same reasoning as sealed_rebuild: this is the third class of live read the
    suite has grown, and the two before it leaked because the attention was on
    the new measurement rather than on what the measurement touched.
    """
    empty_pci = tmp_path / "sealed-pci"
    empty_net = tmp_path / "sealed-net"
    empty_pci.mkdir()
    empty_net.mkdir()
    monkeypatch.setattr(ethernet_pm, "PCI_DEVICES", empty_pci)
    monkeypatch.setattr(wifi_power_save, "NET_DEVICES", empty_net)
    # Same shape for the printing row: point each reader at something that
    # cannot exist, so the readers themselves still run and the machine
    # underneath them does not answer. The unit name matters as much as the
    # paths -- services.is_active() shells out to systemctl either way, and a
    # real unit name would make the row depend on whether the box prints.
    monkeypatch.setattr(printing, "NSSWITCH", tmp_path / "sealed-nsswitch.conf")
    monkeypatch.setattr(printing, "FONT_RULE", tmp_path / "sealed-font-rule.conf")
    monkeypatch.setattr(printing, "SYSTEMD_UNITS", tmp_path / "sealed-units")
    # The scanning row is the same shape and answers off three more files:
    # whether sane is installed, whether the non-free backend is on disk, and
    # what the user's network config names. Unsealed, the row would report the
    # machine running the suite -- and on the box this was written on it would
    # report a configured scanner.
    monkeypatch.setattr(scanning, "SANE_DLL", tmp_path / "sealed-dll.conf")
    monkeypatch.setattr(scanning, "NONFREE_BACKEND", tmp_path / "sealed-backend.so")
    monkeypatch.setattr(scanning, "NETWORK_CONF", tmp_path / "sealed-network.conf")


@pytest.fixture(autouse=True)
def sealed_block_state(monkeypatch, tmp_path):
    """The live-machine reads behind every destructive disk step.

    `blockdev.refuse()` is now on the format path as well as the erase one,
    so any installer test that formats or erases asks the machine running
    the suite whether its devices are mounted. A test that names /dev/sda2
    would pass or fail depending on what the box has plugged in.

    Sealed the sealed_network_state way -- roots repointed, readers left
    running: IN_USE_SOURCES gets an empty file, so busy() really parses and
    really finds nothing.

    ARCHISO_MOUNT is the one that needs care, and the path is deliberately
    one that is never created. Measured 2026-08-30: `findmnt --target` on a
    missing path exits 1 with no output, but on a path that *exists* it
    answers with the filesystem containing it -- so sealing it to tmp_path
    itself would hand back whatever /tmp lives on, which on a box with /tmp
    on disk is a real device node.
    """
    empty_block = tmp_path / "sealed-block"
    empty_block.mkdir()
    empty_mounts = tmp_path / "sealed-mounts"
    empty_mounts.write_text("")
    monkeypatch.setattr(blockdev, "BLOCK", empty_block)
    monkeypatch.setattr(blockdev, "IN_USE_SOURCES", ((str(empty_mounts), None),))
    monkeypatch.setattr(blockdev, "ARCHISO_MOUNT", str(tmp_path / "sealed-never-created"))
    # The installer root menu computes two of its phase rows while drawing,
    # and both of them ask whether /mnt is mounted. Unsealed, every pilot
    # test that opens that menu reports the machine running the suite -- and
    # on a box that happens to have something at /mnt it would report it as
    # installer progress.
    monkeypatch.setattr(disk, "MOUNTS", empty_mounts)
    # MNT itself is deliberately left alone. It is only ever reached behind
    # mounted(), which this empty file already answers no to, so sealing it
    # buys nothing -- and it would weaken a test that pins the production
    # path (`btrfs subvolume set-default /mnt/root`) by turning the constant
    # into a tmp dir. A test that fakes a mount seals MNT itself, and must
    # seal both: disk and base each define their own.


@pytest.fixture
def fake_write():
    """sudo_write replacement that writes directly (tests run unprivileged)."""

    def write(path, content):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")
        return 0

    return write


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHSETUP_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path / "config"
