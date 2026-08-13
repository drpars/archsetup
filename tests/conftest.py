import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from archsetup.core import i18n, mkinitcpio, secureboot  # noqa: E402


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
