"""Package-name audit against the sync databases (archsetup --check-packages).

pacman is faked throughout: the point is the classification logic and the
reporting, not what happens to be in the mirrors on the day this runs.
"""

import subprocess

import pytest

from archsetup.core import i18n, pkgaudit


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
    assert by_name["some-aur-pkg"].status == pkgaudit.AUR_UNCHECKED
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
    assert pkgaudit.report(pkgaudit.audit(tmp_path), sync_dir=tmp_path) == 0
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
    assert pkgaudit.report(pkgaudit.audit(tmp_path), sync_dir=tmp_path) == 1
    out = capsys.readouterr().out
    assert "bridge-utils" in out
    assert "virtualization" in out
    assert "mesa" not in out  # healthy entries stay out of the way


def test_summary_counts_packages_not_entries(tmp_path, capsys):
    """The same package is listed on two axes on purpose (a topical category
    and a "what X needs" one). Counting entries made the summary claim more
    packages than the catalogue has -- one per duplication."""
    _write(tmp_path, "a.toml", """
[[category]]
id = "virtualization"
  [[category.packages]]
  name = "mesa"
  [[category.packages]]
  name = "mlocate"
[[category]]
id = "passthrough"
  [[category.packages]]
  name = "mesa"
  [[category.packages]]
  name = "mlocate"
""")
    findings = pkgaudit.audit(tmp_path)
    assert len(findings) == 4  # per entry: that is what says where to fix it
    pkgaudit.report(findings, sync_dir=tmp_path)
    out = capsys.readouterr().out
    assert "2 paket" in out
    assert "1 eskimiş ad" in out  # mlocate twice is still one stale name
    # ...but both places that carry it are still printed.
    assert out.count("mlocate") == 2


# --- AUR pass ---------------------------------------------------------------

AUR_WORLD = {
    "kmscon-git": {"Name": "kmscon-git", "Maintainer": "someone", "OutOfDate": None},
    "orphaned-pkg": {"Name": "orphaned-pkg", "Maintainer": None, "OutOfDate": None},
    "stale-pkg": {"Name": "stale-pkg", "Maintainer": "x", "OutOfDate": 1735689600},
    # Graduated into extra/: WORLD knows it, so pacman -Si will succeed.
    "mesa": {"Name": "mesa", "Maintainer": "x", "OutOfDate": None},
}


@pytest.fixture
def fake_aur(monkeypatch):
    def info(names):
        return {n: AUR_WORLD[n] for n in names if n in AUR_WORLD}

    monkeypatch.setattr(pkgaudit, "aur_info", info)


def _aur_toml(*names):
    body = "\n".join(
        f'  [[category.packages]]\n  name = "{n}"\n  aur = true' for n in names
    )
    return f'[[category]]\nid = "demo"\n{body}\n'


def test_aur_missing_is_fatal(tmp_path, capsys, fake_aur):
    """A vanished AUR name kills the category exactly like a repo one --
    the helper stops at "target not found" before building anything."""
    _write(tmp_path, "a.toml", _aur_toml("kmscon-git", "deleted-from-aur"))
    assert pkgaudit.report(pkgaudit.audit(tmp_path, aur=True), sync_dir=tmp_path) == 1
    out = capsys.readouterr().out
    assert "deleted-from-aur" in out
    assert "kmscon-git" not in out


def test_aur_package_now_in_the_repos_is_flagged(tmp_path, capsys, fake_aur):
    """aur = true on a graduated package rebuilds from source what already
    ships as a signed binary. Nothing breaks, so it is a warning."""
    _write(tmp_path, "a.toml", _aur_toml("mesa"))
    findings = pkgaudit.audit(tmp_path, aur=True)
    assert [f.status for f in findings] == [pkgaudit.AUR_IN_REPO]
    assert pkgaudit.report(findings, sync_dir=tmp_path) == 0
    assert "mesa" in capsys.readouterr().out


def test_orphan_and_outdated_are_notes_not_failures(tmp_path, fake_aur):
    _write(tmp_path, "a.toml", _aur_toml("orphaned-pkg", "stale-pkg"))
    findings = {f.name: f for f in pkgaudit.audit(tmp_path, aur=True)}
    assert findings["orphaned-pkg"].status == pkgaudit.AUR_OK
    assert findings["orphaned-pkg"].notes == (pkgaudit.ORPHAN,)
    assert findings["stale-pkg"].notes == (pkgaudit.OUTDATED,)


def test_unreachable_aur_does_not_become_a_pile_of_missing(tmp_path, monkeypatch):
    """The difference between "we could not ask" and "it is gone" is the
    whole point: a dropped connection must not condemn every AUR entry."""
    monkeypatch.setattr(pkgaudit, "aur_info", lambda names: None)
    _write(tmp_path, "a.toml", _aur_toml("kmscon-git", "anything"))
    findings = pkgaudit.audit(tmp_path, aur=True)
    assert {f.status for f in findings} == {pkgaudit.AUR_UNCHECKED}
    assert pkgaudit.report(findings, sync_dir=tmp_path) == 0


def test_aur_not_queried_unless_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pkgaudit, "aur_info",
        lambda names: pytest.fail("the AUR must not be touched by default"),
    )
    _write(tmp_path, "a.toml", _aur_toml("kmscon-git"))
    assert pkgaudit.audit(tmp_path)[0].status == pkgaudit.AUR_UNCHECKED


# --- staleness --------------------------------------------------------------


def test_stale_databases_reported(tmp_path):
    import os
    import time

    fresh, old = tmp_path / "extra.db", tmp_path / "g14.db"
    fresh.write_text("x")
    old.write_text("x")
    long_ago = time.time() - 10 * 86400
    os.utime(old, (long_ago, long_ago))

    stale = pkgaudit.stale_databases(days=7, sync_dir=tmp_path)
    assert [name for name, _ in stale] == ["g14"]
    assert stale[0][1] >= 10


def test_a_clean_report_still_warns_about_a_stale_index(tmp_path, capsys):
    """Otherwise the audit can bless a name that died last week -- a wrong
    answer that looks exactly like a right one."""
    import os
    import time

    _write(tmp_path, "a.toml", '[[category]]\nid = "demo"\n'
                               '  [[category.packages]]\n  name = "plocate"\n')
    db = tmp_path / "g14.db"
    db.write_text("x")
    long_ago = time.time() - 30 * 86400
    os.utime(db, (long_ago, long_ago))

    assert pkgaudit.report(pkgaudit.audit(tmp_path), sync_dir=tmp_path) == 0
    assert "g14" in capsys.readouterr().out


def test_a_graduated_package_is_not_reported_as_missing(tmp_path, monkeypatch):
    """The AUR deletes an entry once the package lands in the repos.

    Looking only at the AUR, that is indistinguishable from deletion -- and
    reporting it as fatal fires on exactly the packages in the best shape.
    This was a real false positive: waydroid and webapp-manager both moved
    into extra/ and were flagged as unresolvable.
    """
    monkeypatch.setattr(pkgaudit, "aur_info", lambda names: {})
    _write(tmp_path, "a.toml", _aur_toml("mesa", "really-gone"))

    findings = {f.name: f for f in pkgaudit.audit(tmp_path, aur=True)}
    assert findings["mesa"].status == pkgaudit.AUR_IN_REPO
    assert findings["really-gone"].status == pkgaudit.AUR_MISSING


def test_dead_aur_name_does_not_claim_the_repo_packages_too(tmp_path, capsys,
                                                            fake_aur):
    """pacman and the AUR helper run separate transactions, so the report
    must not say the repo half of a category dies with the AUR half."""
    _write(tmp_path, "a.toml", '[[category]]\nid = "demo"\n'
                               '  [[category.packages]]\n  name = "plocate"\n'
                               '  [[category.packages]]\n  name = "gone-pkg"\n'
                               '  aur = true\n')
    assert pkgaudit.report(pkgaudit.audit(tmp_path, aur=True),
                           sync_dir=tmp_path) == 1
    out = capsys.readouterr().out
    assert "gone-pkg" in out
    assert "plocate" not in out


# --- what the summary line claims -------------------------------------------


def test_summary_says_the_aur_was_checked_when_it_was(tmp_path, capsys, fake_aur):
    """The count used to be printed under a fixed "(unchecked)" label, so a
    run that had just queried the AUR -- and listed its findings a few lines
    above -- still ended with "0 AUR (unchecked)" and read like a skip."""
    _write(tmp_path, "a.toml", _aur_toml("kmscon-git", "orphaned-pkg"))
    pkgaudit.report(pkgaudit.audit(tmp_path, aur=True), sync_dir=tmp_path,
                    aur_requested=True)

    out = capsys.readouterr().out
    assert i18n.t("pkgaudit.summary_aur_checked", count=2) in out
    assert i18n.t("pkgaudit.summary_aur_unchecked", count=0) not in out


def test_summary_counts_the_aur_entries_left_unchecked(tmp_path, capsys):
    _write(tmp_path, "a.toml", _aur_toml("kmscon-git", "orphaned-pkg"))
    pkgaudit.report(pkgaudit.audit(tmp_path), sync_dir=tmp_path)

    out = capsys.readouterr().out
    assert i18n.t("pkgaudit.summary_aur_unchecked", count=2) in out
    assert i18n.t("pkgaudit.aur_hint") in out


def test_unreachable_aur_does_not_suggest_the_flag_it_was_given(tmp_path, capsys,
                                                                monkeypatch):
    """--aur was passed and the lookup failed. Printing "to check the AUR
    too, pass --aur" on top of that reads as if the flag were ignored."""
    monkeypatch.setattr(pkgaudit, "aur_info", lambda names: None)
    _write(tmp_path, "a.toml", _aur_toml("kmscon-git"))
    pkgaudit.report(pkgaudit.audit(tmp_path, aur=True), sync_dir=tmp_path,
                    aur_requested=True)

    assert i18n.t("pkgaudit.aur_hint") not in capsys.readouterr().out


def test_summary_leaves_out_the_aur_clause_when_there_are_no_aur_entries(
        tmp_path, capsys):
    _write(tmp_path, "a.toml", '[[category]]\nid = "demo"\n'
                               '  [[category.packages]]\n  name = "plocate"\n')
    pkgaudit.report(pkgaudit.audit(tmp_path), sync_dir=tmp_path)

    out = capsys.readouterr().out
    assert "AUR" not in out
