import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from archsetup.core import (  # noqa: E402
    ethernet_pm,
    i18n,
    mkinitcpio,
    secureboot,
    wifi_power_save,
)


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
    """The live-machine reads behind the two network menu rows.

    Those rows carry a computed description, so their state readers run while
    the menu is being *drawn*: `ethernet_pm.status()` walks /sys/bus/pci and
    `wifi_power_save.status()` walks /sys/class/net and then shells out to
    `iw` once per interface. Any pilot test that walks into the network menu
    would answer differently per machine, and on a box with no wifi it would
    also spawn a subprocess for nothing.

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
