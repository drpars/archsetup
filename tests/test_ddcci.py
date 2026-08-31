"""DDC/CI backlight task: the gates, the four files, and the row.

The measurements this file pins were taken on 2026-08-31 against the machine
the setup runs on (an nvidia desktop, kernel 7.1.11-zen1, DKMS ddcci 0.4.5):
the backlight device is named ddcci2, the built modules land as
updates/dkms/ddcci.ko.zst next to three other DKMS modules, and the unit reads
enabled + *inactive* while the setup works.
"""

from pathlib import Path

import pytest

from archsetup.core import ddcci, dkms, i18n, pacman, prompt, sysedit


# --------------------------------------------------------------------------
# what gets shipped
# --------------------------------------------------------------------------


def test_every_asset_exists_and_is_not_empty():
    """The task distributes files; a missing one is a silent half-install."""
    for asset, _, _ in ddcci.files():
        path = ddcci.ASSETS / asset
        assert path.is_file(), path
        assert path.stat().st_size > 0, path


def test_helper_is_the_path_the_unit_execs():
    """The unit's ExecStart and the helper's destination are one fact, twice.

    Renaming the helper without editing the unit gives a unit that fails with
    ENOENT, which systemd reports at boot and nowhere else. `systemd-analyze
    verify` catches it only on a machine where the file is already installed,
    so the check belongs here.

    Compared by name rather than by full path because conftest's seal repoints
    HELPER into tmp_path and keeps the basename; the directory is pinned off
    the asset itself, which is the half that says where this ships to.
    """
    unit = (ddcci.ASSETS / "ddcci-attach.service").read_text(encoding="utf-8")
    exec_line = next(
        line for line in unit.splitlines() if line.startswith("ExecStart=")
    )
    execstart = Path(exec_line.split("=", 1)[1])
    assert execstart.name == ddcci.HELPER.name
    assert execstart.parent == Path("/usr/local/bin")


def test_udev_rule_restarts_the_unit_this_module_enables():
    """Same shape: the rule names the unit, and the module enables it."""
    rule = (ddcci.ASSETS / "99-ddcci-attach.rules").read_text(encoding="utf-8")
    assert ddcci.UNIT_NAME in rule
    # --no-block or a udev worker is held for as long as the helper looks,
    # which is up to 15 s of detect tries.
    assert "--no-block" in rule


def test_modules_load_names_both_modules():
    """The package loads neither module by itself; this file is why they load."""
    conf = (ddcci.ASSETS / "ddcci.conf").read_text(encoding="utf-8").split()
    assert conf == ["ddcci", "ddcci-backlight"]


def test_helper_asks_ddcutil_and_not_the_drm_symlink():
    """The reason this task exists at all, pinned.

    ddcci-discover was rejected because it derives the i2c bus from
    /sys/class/drm/<connector>/ddc and the nvidia driver does not create that
    symlink. A future edit that "simplifies" the helper back onto the symlink
    would restore exactly the failure that made the whole thing not work, and
    it would look like a cleanup.
    """
    helper = (ddcci.ASSETS / "ddcci-attach.sh").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in helper.splitlines() if not line.lstrip().startswith("#")
    )
    assert "ddcutil detect" in code
    assert "/sys/class/drm" not in code


def test_helper_is_shipped_executable():
    """0755 for the helper, 0644 for the rest -- the execute bit is the unit."""
    modes = {asset: mode for asset, _, mode in ddcci.files()}
    assert modes["ddcci-attach.sh"] == "0755"
    assert modes["ddcci-attach.service"] == "0644"


# --------------------------------------------------------------------------
# the DKMS module reading
# --------------------------------------------------------------------------


def test_module_built_is_not_satisfied_by_the_directory(tmp_path, monkeypatch):
    """updates/dkms exists as soon as ANY DKMS module builds.

    Measured on the desktop: acpi_call, nvidia, openrazer-driver and ddcci all
    land in that one directory. A check on the directory would pass on a
    machine where every DKMS package but this one built.
    """
    monkeypatch.setattr(dkms, "KERNEL_MODULES", tmp_path)
    modules = tmp_path / "7.1.11-zen1-1-zen" / "updates" / "dkms"
    modules.mkdir(parents=True)
    (modules / "nvidia.ko.zst").write_text("")
    assert dkms.module_built("7.1.11-zen1-1-zen", "ddcci") is False

    (modules / "ddcci.ko.zst").write_text("")
    assert dkms.module_built("7.1.11-zen1-1-zen", "ddcci") is True


def test_module_built_survives_another_compression_suffix(tmp_path, monkeypatch):
    """The suffix is a kernel build option, so the glob stops at .ko."""
    monkeypatch.setattr(dkms, "KERNEL_MODULES", tmp_path)
    modules = tmp_path / "6.1.0" / "updates" / "dkms"
    modules.mkdir(parents=True)
    (modules / "ddcci.ko").write_text("")
    assert dkms.module_built("6.1.0", "ddcci") is True


def test_module_built_on_a_kernel_with_no_dkms_at_all(tmp_path, monkeypatch):
    monkeypatch.setattr(dkms, "KERNEL_MODULES", tmp_path)
    assert dkms.module_built("6.1.0", "ddcci") is False


def test_headers_package_comes_from_the_kernel(tmp_path, monkeypatch):
    monkeypatch.setattr(dkms, "KERNEL_MODULES", tmp_path)
    release = tmp_path / "7.1.11-zen1-1-zen"
    release.mkdir()
    (release / "pkgbase").write_text("linux-zen\n")
    assert dkms.headers_package("7.1.11-zen1-1-zen") == "linux-zen-headers"
    assert dkms.headers_present("7.1.11-zen1-1-zen") is False
    (release / "build").mkdir()
    assert dkms.headers_present("7.1.11-zen1-1-zen") is True


def test_headers_package_is_none_when_the_kernel_did_not_say(tmp_path, monkeypatch):
    """No pkgbase means no name to install, and a guess is worse than a stop."""
    monkeypatch.setattr(dkms, "KERNEL_MODULES", tmp_path)
    (tmp_path / "6.1.0").mkdir()
    assert dkms.headers_package("6.1.0") is None


# --------------------------------------------------------------------------
# the menu row
# --------------------------------------------------------------------------


def test_status_counts_the_files_and_names_the_device(tmp_path, monkeypatch):
    i18n.load("en")
    backlight = tmp_path / "backlight"
    backlight.mkdir()
    (backlight / "ddcci2").mkdir()
    # A laptop panel sits in the same directory and is not this task's device.
    (backlight / "intel_backlight").mkdir()
    monkeypatch.setattr(ddcci, "BACKLIGHT", backlight)
    monkeypatch.setattr(ddcci, "HELPER", tmp_path / "helper.sh")
    (tmp_path / "helper.sh").write_text("")

    row = ddcci.status()
    assert "1/4" in row
    assert "ddcci2" in row
    assert "intel_backlight" not in row


def test_status_reproduces_the_working_machine(tmp_path, monkeypatch):
    """The row as it reads on the desktop the setup actually runs on.

    Measured there on 2026-08-31: all four files in place with the same
    sha256s this repo ships, and /sys/class/backlight/ddcci2 present -- while
    `systemctl is-active ddcci-attach.service` says *inactive*, which is why
    that reading is not in this line.
    """
    i18n.load("en")
    backlight = tmp_path / "backlight"
    backlight.mkdir()
    (backlight / "ddcci2").mkdir()
    monkeypatch.setattr(ddcci, "BACKLIGHT", backlight)
    for name, attr in (
        ("ddcci-attach.sh", "HELPER"),
        ("ddcci-attach.service", "UNIT"),
        ("99-ddcci-attach.rules", "UDEV_RULES"),
        ("ddcci.conf", "MODULES_LOAD"),
    ):
        path = tmp_path / name
        path.write_text(
            (ddcci.ASSETS / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
        monkeypatch.setattr(ddcci, attr, path)

    row = ddcci.status()
    assert "4/4" in row
    assert "device: ddcci2" in row


def test_status_says_no_device_on_a_machine_without_one():
    """Sealed by conftest, so this is the row on a machine with nothing set up."""
    i18n.load("en")
    row = ddcci.status()
    assert "0/4" in row
    assert "no device" in row


def test_status_takes_no_subprocess(monkeypatch):
    """The row is computed while the menu draws; ddcutil detect costs seconds."""
    def explode(*args, **kwargs):  # pragma: no cover - the point is not calling it
        raise AssertionError("status() spawned a subprocess")

    monkeypatch.setattr(pacman, "run", explode)
    monkeypatch.setattr(pacman.subprocess, "run", explode)
    monkeypatch.setattr(pacman.subprocess, "call", explode)
    ddcci.status()


def test_status_does_not_read_the_unit_state():
    """A working setup reads `inactive`, so the unit must not be in the row.

    Measured on the desktop where it works: Type=exec, the helper exits, and
    `systemctl is-active` answers inactive while /sys/class/backlight/ddcci2 is
    present. This pins the shape rather than the wording: no call into
    services, whose is_active() is the thing that would be reached for.
    """
    source = Path(ddcci.__file__).read_text(encoding="utf-8")
    body = source.split("def status(")[1].split("\ndef ")[0]
    assert "is_active" not in body


# --------------------------------------------------------------------------
# placing one file
# --------------------------------------------------------------------------


def test_place_leaves_a_correct_file_completely_alone(tmp_path, monkeypatch, runlog):
    """A re-run that changes nothing must not ask for root.

    Four unconditional chmods are four graphical approval windows on a machine
    where sudo goes through askpass, for a run that moved no bytes.
    """
    dest = tmp_path / "ddcci.conf"
    dest.write_text(
        (ddcci.ASSETS / "ddcci.conf").read_text(encoding="utf-8"), encoding="utf-8"
    )
    dest.chmod(0o644)
    monkeypatch.setattr(ddcci, "run", runlog)

    assert ddcci._place("ddcci.conf", dest, "0644") == (0, False)
    assert runlog.calls == []


def test_place_fixes_a_mode_that_is_wrong(tmp_path, monkeypatch, runlog):
    """The helper arrives 0644 out of `sudo tee`; without this it never execs."""
    dest = tmp_path / "ddcci-attach.sh"
    dest.write_text(
        (ddcci.ASSETS / "ddcci-attach.sh").read_text(encoding="utf-8"), encoding="utf-8"
    )
    dest.chmod(0o644)
    monkeypatch.setattr(ddcci, "run", runlog)

    rc, changed = ddcci._place("ddcci-attach.sh", dest, "0755")
    assert (rc, changed) == (0, False)
    assert [" ".join(cmd) for cmd in runlog.calls] == [
        f"sudo chmod 0755 {dest}"
    ]


def test_place_reports_a_missing_asset_instead_of_writing_nothing(
    tmp_path, monkeypatch, capsys
):
    i18n.load("en")
    monkeypatch.setattr(ddcci, "ASSETS", tmp_path / "not-shipped")
    rc, changed = ddcci._place("ddcci.conf", tmp_path / "dest.conf", "0644")
    assert (rc, changed) == (1, False)
    assert not (tmp_path / "dest.conf").exists()


# --------------------------------------------------------------------------
# configure(): the gates before anything is installed
# --------------------------------------------------------------------------


@pytest.fixture
def no_install(monkeypatch):
    """pacman.install that fails the test if it is reached."""
    def explode(repo, aur):
        raise AssertionError(f"installed {repo} / {aur}")

    monkeypatch.setattr(pacman, "install", explode)


def test_configure_stops_when_the_headers_package_cannot_be_named(
    monkeypatch, no_install, capsys
):
    """A DKMS install with no headers succeeds and produces no module."""
    monkeypatch.setattr(dkms, "headers_present", lambda release: False)
    monkeypatch.setattr(dkms, "headers_package", lambda release: None)
    assert ddcci.configure() == 1


def test_configure_adds_the_headers_to_the_repo_transaction(monkeypatch):
    """Named headers are installed alongside rather than being a refusal."""
    seen = {}

    def fake_install(repo, aur):
        seen["repo"] = repo
        seen["aur"] = aur
        return 1  # stop the run here; the transaction is what this pins

    monkeypatch.setattr(dkms, "headers_present", lambda release: False)
    monkeypatch.setattr(dkms, "headers_package", lambda release: "linux-zen-headers")
    monkeypatch.setattr(prompt, "ask_yes", lambda question: True)
    monkeypatch.setattr(pacman, "install", fake_install)

    assert ddcci.configure() == 1
    assert "linux-zen-headers" in seen["repo"]
    assert "ddcutil" in seen["repo"]
    assert seen["aur"] == ["ddcci-driver-linux-dkms-git"]


def test_configure_asks_before_paying_for_a_dkms_package(
    monkeypatch, no_install
):
    """The cost is ongoing -- a rebuild per kernel -- so it is not silent."""
    monkeypatch.setattr(dkms, "headers_present", lambda release: True)
    monkeypatch.setattr(prompt, "ask_yes", lambda question: False)
    assert ddcci.configure() == 0


def test_configure_places_four_files_and_enables_the_unit(
    monkeypatch, tmp_path, runlog
):
    """The whole write path, unprivileged: four files, then reload and enable."""
    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.setattr(ddcci, "HELPER", dest / "ddcci-attach.sh")
    monkeypatch.setattr(ddcci, "UNIT", dest / "ddcci-attach.service")
    monkeypatch.setattr(ddcci, "UDEV_RULES", dest / "99-ddcci-attach.rules")
    monkeypatch.setattr(ddcci, "MODULES_LOAD", dest / "ddcci.conf")

    monkeypatch.setattr(dkms, "headers_present", lambda release: True)
    monkeypatch.setattr(dkms, "module_built", lambda release, module: True)
    monkeypatch.setattr(prompt, "ask_yes", lambda question: True)
    monkeypatch.setattr(pacman, "install", lambda repo, aur: 0)
    # sysedit writes through `sudo tee`; the test writes directly instead.
    monkeypatch.setattr(
        sysedit,
        "sudo_write",
        lambda path, content: (Path(path).write_text(content, encoding="utf-8"), 0)[1],
    )
    monkeypatch.setattr(ddcci, "run", runlog)
    monkeypatch.setattr("archsetup.core.services.run", runlog)

    rc = ddcci.configure()
    assert rc == 0

    for asset, path, _ in ddcci.files():
        assert path.read_text(encoding="utf-8") == (
            ddcci.ASSETS / asset
        ).read_text(encoding="utf-8")

    joined = [" ".join(cmd) for cmd in runlog.calls]
    assert any("chmod 0755" in cmd and "ddcci-attach.sh" in cmd for cmd in joined)
    assert any("udevadm control --reload-rules" in cmd for cmd in joined)
    assert any("systemctl daemon-reload" in cmd for cmd in joined)
    assert any(f"enable --now {ddcci.UNIT_NAME}" in cmd for cmd in joined)


def test_configure_warns_but_continues_when_dkms_left_no_module(
    monkeypatch, tmp_path, runlog, capsys
):
    """The next kernel update rebuilds it, so the files are still worth placing."""
    i18n.load("en")
    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.setattr(ddcci, "HELPER", dest / "ddcci-attach.sh")
    monkeypatch.setattr(ddcci, "UNIT", dest / "ddcci-attach.service")
    monkeypatch.setattr(ddcci, "UDEV_RULES", dest / "99-ddcci-attach.rules")
    monkeypatch.setattr(ddcci, "MODULES_LOAD", dest / "ddcci.conf")
    monkeypatch.setattr(dkms, "headers_present", lambda release: True)
    monkeypatch.setattr(dkms, "module_built", lambda release, module: False)
    monkeypatch.setattr(prompt, "ask_yes", lambda question: True)
    monkeypatch.setattr(pacman, "install", lambda repo, aur: 0)
    monkeypatch.setattr(
        sysedit,
        "sudo_write",
        lambda path, content: (Path(path).write_text(content, encoding="utf-8"), 0)[1],
    )
    monkeypatch.setattr(ddcci, "run", runlog)
    monkeypatch.setattr("archsetup.core.services.run", runlog)

    ddcci.configure()
    out = capsys.readouterr().out
    assert "no ddcci module" in out
    assert (dest / "ddcci-attach.sh").exists()
