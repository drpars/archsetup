"""Package-name audit against the sync databases (archsetup --check-packages).

pacman is faked throughout: the point is the classification logic and the
reporting, not what happens to be in the mirrors on the day this runs.
"""

import subprocess

import pytest

from archsetup.core import pkgaudit


# name -> (exists exactly, is a group, what -Sddp resolves it to or None)
WORLD = {
    "plocate":      (True,  False, "plocate"),
    "7zip":         (True,  False, "7zip"),
    "mesa":         (True,  False, "mesa"),
    "sddm":         (True,  False, "sddm"),
    "plasma":       (False, True,  None),
    "mlocate":      (False, False, "plocate"),
    "p7zip":        (False, False, "7zip"),
    "bridge-utils": (False, False, None),
    "ghost":        (False, False, None),
}


@pytest.fixture(autouse=True)
def fake_pacman(monkeypatch):
    def run(cmd, **kwargs):
        flag, name = cmd[1], cmd[-1]
        exists, is_group, resolves = WORLD.get(name, (False, False, None))
        if flag == "-Si":
            return subprocess.CompletedProcess(cmd, 0 if exists else 1, "", "")
        if flag == "-Sg":
            return subprocess.CompletedProcess(cmd, 0 if is_group else 1, "", "")
        if flag == "-Sddp":
            if resolves is None:
                return subprocess.CompletedProcess(cmd, 1, "", "error")
            return subprocess.CompletedProcess(cmd, 0, f"{resolves}\n", "")
        raise AssertionError(f"unexpected pacman call: {cmd}")

    monkeypatch.setattr(pkgaudit.subprocess, "run", run)


@pytest.mark.parametrize(
    "name,status,resolved",
    [
        ("plocate", pkgaudit.OK, "plocate"),
        ("plasma", pkgaudit.GROUP, "plasma"),
        ("mlocate", pkgaudit.PROVIDED, "plocate"),
        ("p7zip", pkgaudit.PROVIDED, "7zip"),
        ("bridge-utils", pkgaudit.MISSING, ""),
    ],
)
def test_classify(name, status, resolved):
    assert pkgaudit.classify(name) == (status, resolved)


def test_aur_is_not_queried(monkeypatch):
    """AUR names cannot be answered offline, so they are not guessed at."""
    monkeypatch.setattr(
        pkgaudit.subprocess, "run",
        lambda *a, **k: pytest.fail("pacman should not be called for AUR"),
    )
    assert pkgaudit.classify("anything-at-all", aur=True) == (pkgaudit.AUR, "")


def test_a_group_is_not_reported_as_missing():
    """`pacman -Si plasma` fails for groups; treating that as missing would
    flag a perfectly installable entry."""
    assert pkgaudit.classify("plasma")[0] == pkgaudit.GROUP


def _write(dir_path, name, text):
    (dir_path / name).write_text(text, encoding="utf-8")


def test_audit_walks_categories_and_display_managers(tmp_path):
    _write(tmp_path, "apps.toml", """
[[category]]
id = "console"
  [[category.packages]]
  name = "plocate"
  [[category.packages]]
  name = "mlocate"
  [[category.packages]]
  name = "some-aur-pkg"
  aur = true
""")
    _write(tmp_path, "displaymanagers.toml", """
[[dm]]
id = "sddm"
package = "sddm"
service = "sddm.service"
[[dm]]
id = "ghost"
service = "ghost.service"
""")

    findings = pkgaudit.audit(tmp_path)
    by_name = {f.name: f for f in findings}

    assert by_name["mlocate"].status == pkgaudit.PROVIDED
    assert by_name["mlocate"].resolved == "plocate"
    assert by_name["some-aur-pkg"].status == pkgaudit.AUR
    # A display manager with no explicit package falls back to its id.
    assert by_name["ghost"].status == pkgaudit.MISSING
    assert by_name["sddm"].status == pkgaudit.OK


def test_audit_recurses_into_subdirectories(tmp_path):
    (tmp_path / "install").mkdir()
    _write(tmp_path / "install", "extras.toml", """
[[category]]
id = "extras"
  [[category.packages]]
  name = "bridge-utils"
""")
    findings = pkgaudit.audit(tmp_path)
    assert [f.status for f in findings] == [pkgaudit.MISSING]
    assert findings[0].source == "install/extras.toml"


def test_report_fails_only_on_unresolvable_names(tmp_path, capsys):
    """A stale-but-working name is a warning; pacman still installs it."""
    _write(tmp_path, "a.toml", """
[[category]]
id = "demo"
  [[category.packages]]
  name = "mlocate"
""")
    assert pkgaudit.report(pkgaudit.audit(tmp_path)) == 0
    assert "mlocate" in capsys.readouterr().out


def test_report_names_the_categories_that_break(tmp_path, capsys):
    """One dead entry takes its whole category down, so the report has to
    say which categories those are -- not just which packages."""
    _write(tmp_path, "a.toml", """
[[category]]
id = "virtualization"
  [[category.packages]]
  name = "bridge-utils"
  [[category.packages]]
  name = "mesa"
""")
    assert pkgaudit.report(pkgaudit.audit(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "bridge-utils" in out
    assert "virtualization" in out
    assert "mesa" not in out  # healthy entries stay out of the way
