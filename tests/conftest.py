import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from archsetup.core import i18n  # noqa: E402


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
