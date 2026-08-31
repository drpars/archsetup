"""DDC/CI backlight for external monitors: the discovery half, not just a package.

An external monitor has no backlight device, so the brightness keys, waybar's
backlight module and hypridle's dimming all have nothing to act on. The ddcci
kernel driver creates one by talking DDC/CI over the monitor's i2c bus -- and
that is where every attempt stops, because since kernel 6.8 dropped
I2C_CLASS_DDC the driver cannot find the bus by itself. The bus has to be named
from userspace, and naming it is the whole job.

Why the package alone is not the feature, measured on 2026-08-31: the AUR
package ships 60 file entries and every one of them is under /usr/src/ddcci
(the other two are the /usr and /usr/src directories themselves). Nothing in
udev, nothing in modules-load.d, nothing in /usr/bin. The four files that make
it work belong to no package at all -- `pacman -Qo` answers "No package owns"
for each. Cataloguing the package and stopping there buys a source tree.

Why the discovery half is archsetup's now, having been declined on 2026-08-28:
the rejection was about the only candidate that existed then. ddcci-discover
derives the i2c bus from /sys/class/drm/<connector>/ddc, the nvidia driver does
not create that symlink, and the program returned SUCCESS without touching a
single bus. The helper shipped here asks a different question: it finds the bus
by *talking* to it (`ddcutil detect --skip-ddc-checks --disable-dynamic-sleep
--brief`) and never reads the symlink. That is what makes it work on nvidia,
and it is measured rather than argued -- on the machine it was written for it
reported "ddcci bound on i2c-2 after 2 reload(s)" and left
/sys/class/backlight/ddcci2 behind.

What it costs, so the choice is not made silently: an AUR package, a DKMS build
on every kernel update (and, on a Secure Boot machine, a UKI regenerated and
re-signed after it), plus ddcutil from the repositories and four files this
task distributes. ddcutil is a real dependency and not a package-level one --
`Required By: None`, `Depends On: dkms` -- so it is installed here explicitly
rather than left to resolve.

Two things this task deliberately does not do. It does not go into the base
install: a machine with no external monitor gets a DKMS build and four files
for nothing. And it does not report the service as the proof -- see status().

The four files and their modes are in files() below; the bytes are in
data/ddcci/ and are byte-identical to the setup they were measured on, apart
from the provenance comment in the script header.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import paths
from . import dkms, i18n, pacman, prompt, services, sysedit
from .pacman import run

t = i18n.t

ASSETS = paths.DATA_DIR / "ddcci"

REPO_PACKAGES = ("ddcutil",)
AUR_PACKAGES = ("ddcci-driver-linux-dkms-git",)

# The module the DKMS package is expected to leave behind; ddcci-backlight is
# built alongside it and is not checked separately, because a build that
# produced one produced both (measured: both .ko.zst in the same directory).
MODULE = "ddcci"

HELPER = Path("/usr/local/bin/ddcci-attach.sh")
UNIT_NAME = "ddcci-attach.service"
UNIT = Path("/etc/systemd/system") / UNIT_NAME
UDEV_RULES = Path("/etc/udev/rules.d/99-ddcci-attach.rules")
MODULES_LOAD = Path("/etc/modules-load.d/ddcci.conf")

BACKLIGHT = Path("/sys/class/backlight")

def files() -> tuple[tuple[str, Path, str], ...]:
    """(asset name, destination, mode) for each of the four.

    Derived from the constants above rather than frozen next to them, because
    the menu row below reads these paths while it is being drawn and the test
    suite therefore has to be able to repoint them (conftest seals this row the
    way it seals the network ones). A module-level tuple would capture the real
    /etc paths at import and quietly ignore the seal.

    /usr/local/bin rather than /usr/bin because nothing here is
    package-managed and pacman owns the latter; the unit's ExecStart names this
    path, so the two move together or not at all -- there is a test for that.

    The sibling .bak that write_with_backup leaves behind is read by nothing in
    these four directories: systemd-modules-load globs *.conf, udev reads
    *.rules, systemd loads only known unit suffixes, and a non-executable
    .sh.bak in /usr/local/bin is not what anyone execs.
    """
    return (
        ("ddcci-attach.sh", HELPER, "0755"),
        ("ddcci-attach.service", UNIT, "0644"),
        ("99-ddcci-attach.rules", UDEV_RULES, "0644"),
        ("ddcci.conf", MODULES_LOAD, "0644"),
    )


def backlight_devices() -> list[str]:
    """The ddcci backlight devices that exist right now.

    This is the only reading that answers "did it work", which is why it is a
    function and not an inline glob: the driver names them ddcci<bus>, so the
    prefix is the match and the number is not predictable from here.
    """
    try:
        return sorted(
            entry.name
            for entry in BACKLIGHT.iterdir()
            if entry.name.startswith("ddcci")
        )
    except OSError:
        return []


def _placed() -> list[Path]:
    return [dest for _, dest, _ in files() if dest.exists()]


def status() -> str:
    """One menu line: the four files, and the device that proves the point.

    The service is not in this line, and that is the measured part. The unit is
    Type=exec and the helper exits when it is done, so a working machine reads
    `is-active: inactive` -- on the machine this was taken from, enabled and
    inactive, with /sys/class/backlight/ddcci2 present. A row built on the unit
    state would call a working setup broken, and would keep saying so.

    Files only, plus one readdir: no subprocess. The menu computes this row
    while drawing, and `ddcutil detect` costs seconds even when it succeeds.
    """
    devices = backlight_devices()
    device = (
        t("ddcci.status_device", devices=", ".join(devices))
        if devices
        else t("ddcci.status_no_device")
    )
    return t(
        "ddcci.status_line",
        files=len(_placed()),
        total=len(files()),
        device=device,
    )


def _place(asset: str, dest: Path, mode: str) -> tuple[int, bool]:
    """Write one asset, then set its mode. Returns (rc, changed)."""
    try:
        content = (ASSETS / asset).read_text(encoding="utf-8")
    except OSError as exc:
        print(t("ddcci.asset_missing", path=ASSETS / asset, error=exc))
        return 1, False

    rc, changed = sysedit.write_with_backup(dest, content)
    # write_with_backup goes through `sudo tee`, which leaves 0644 behind, and
    # for the helper the execute bit is the difference between a working unit
    # and one that fails with EACCES -- so the mode is set here rather than
    # assumed.
    #
    # Asked first rather than set unconditionally, and not because chmod is
    # expensive: on a machine where sudo goes through a graphical askpass, four
    # unconditional chmods are four approval windows on a re-run that changed
    # nothing. The reading is one unprivileged stat.
    want = int(mode, 8)
    try:
        current = dest.stat().st_mode & 0o777
    except OSError:
        current = None
    if current != want:
        rc |= run(["sudo", "chmod", mode, str(dest)])
    return rc, changed


def configure() -> int:
    # Headers first, before anything is installed. A DKMS package with no
    # headers to build against installs successfully and produces no module,
    # so the honest outcomes are "stop and say what is needed" or "install the
    # headers too" -- never "proceed and let it look like it worked".
    release = os.uname().release
    repo_packages = [*REPO_PACKAGES]
    if not dkms.headers_present(release):
        headers = dkms.headers_package(release)
        if headers is None:
            print(t("dkms.headers_unknown", release=release))
            return 1
        print(t("dkms.headers_needed", package=headers))
        repo_packages.append(headers)

    print(t("ddcci.plan", pkg=AUR_PACKAGES[0]))
    if not prompt.ask_yes(t("ddcci.plan_q")):
        print(t("msg.cancelled"))
        return 0

    rc = pacman.install(repo_packages, [*AUR_PACKAGES])
    if rc != 0:
        return rc

    # The module, not the package. This is the reading the headers gate exists
    # to protect, and it is worth taking even when the gate passed: a DKMS
    # build can fail for its own reasons and pacman still reports success.
    # A warning rather than a return: the four files below are still worth
    # placing, because the next kernel update rebuilds the module and then a
    # machine with the files already in place needs nothing further.
    if not dkms.module_built(release, MODULE):
        print(t("ddcci.module_missing", release=release, module=MODULE))

    rc_files = 0
    changed = False
    for asset, dest, mode in files():
        file_rc, file_changed = _place(asset, dest, mode)
        rc_files |= file_rc
        changed = changed or file_changed
    rc |= rc_files
    if rc_files != 0:
        print(t("ddcci.files_failed"))
        return rc

    if changed:
        rc |= run(["sudo", "udevadm", "control", "--reload-rules"])
        rc |= run(["sudo", "systemctl", "daemon-reload"])

    # enable --now is the real path: it is what a reboot will do, and running
    # the helper by hand instead would prove the script and not the unit.
    rc |= services.enable_now(UNIT_NAME)

    # Written and started are not the same as working. The helper walks up to
    # DETECT_TRIES one-second tries before giving up, so the device may not be
    # there the instant the unit returns; the message says which reading to
    # take rather than pretending this one is final.
    devices = backlight_devices()
    if devices:
        print(t("ddcci.done", devices=", ".join(devices)))
    else:
        print(t("ddcci.no_device_yet", unit=UNIT_NAME))
    print(t("ddcci.undo", pkg=AUR_PACKAGES[0], unit=UNIT_NAME))
    print(status())
    return rc
