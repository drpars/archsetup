"""Giving a kernel installed after the fact the boot entry the others have.

mkinitcpio generates the preset for a newly installed kernel itself, from
/usr/share/mkinitcpio/hook.preset with %PKGBASE% substituted, and that template
ships `default_image` active with `default_uki` commented out. Measured
2026-08-13 on this laptop: neither linux-g14 nor linux-ogc ships a preset at
all, both files on disk belong to no package, and installing linux-zen beside
an already-UKI machine produced a 252 MiB /boot/initramfs-linux-zen.img and no
/efi/EFI/Linux/arch-linux-zen.efi. systemd-boot enumerates that directory, so
the new kernel was simply not in the boot menu. pacman reported success, the
rebuild reported success, and the only symptom is an entry that never appears.

installer.chroot.gen_uki performs this same conversion, but it runs against
/mnt and it runs once. This is its post-install half; the four preset lines
they both rewrite live in core.mkinitcpio rather than once per side.

What establishes that this is a UKI machine is another preset, not a setting.
If something under /etc/mkinitcpio.d already builds a UKI then whatever makes
that image bootable -- systemd-boot's discovery, a signed ESP, an EFI entry --
does the same for this one, and no bootloader has to be identified here. With
no such preset the task refuses rather than guessing: converting a machine to
UKI output is a bootloader change, and the installer is where that is decided.

The reference decides which passes become UKIs, the preset being fixed decides
which passes exist. A machine that builds only a default UKI gets only a
default UKI for the new kernel, so this task can never be the reason a fallback
image -- 214 MiB against 33 MiB, measured on a plain linux-zen install -- lands
on an ESP nobody sized for one. A pass left behind that way is named rather
than silently skipped.

Deliberately not predicted: the size of the image about to be built. The two
kernels on this laptop differ by ~109 MiB because linux-zen carries nouveau and
linux-g14 does not, so a figure derived from the existing UKI would read as a
measurement and be wrong by a factor -- the same mistake as the three UKI sizes
once derived from `df`. Free space is measured and printed; the size is not.

Undo is the dated copy of the preset beside itself. Safe here, unlike the
libvirt hook directory that made write_with_backup take the argument at all:
mkinitcpio's own glob is *.preset, so a file ending in a date is not a second
preset. Measured neighbour on this machine: linux-zen.preset.sablon, left over
from the hand edit, which mkinitcpio has never read.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from . import i18n, mkinitcpio, sysedit
from .prompt import ask_yes

t = i18n.t

ROOT = Path("/")
CMDLINE = Path("/etc/kernel/cmdline")
CMDLINE_D = Path("/etc/cmdline.d")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def uki_passes(text: str) -> list[str]:
    """Passes this preset builds as a UKI."""
    return [
        name
        for name in mkinitcpio.preset_passes(text)
        if mkinitcpio.preset_value(text, f"{name}_uki")
    ]


def image_passes(text: str) -> list[str]:
    """Passes that write a plain image and no UKI beside it.

    A pass with both lines active is not one of these: it already produces a
    boot entry, and the redundant .img it also writes is somebody's deliberate
    arrangement rather than the gap this task closes.
    """
    return [
        name
        for name in mkinitcpio.preset_passes(text)
        if mkinitcpio.preset_value(text, f"{name}_image")
        and not mkinitcpio.preset_value(text, f"{name}_uki")
    ]


def _cmdline_present() -> bool:
    """Whether a command line exists for the UKI to embed.

    With neither file mkinitcpio falls back to /proc/cmdline, which on a
    running machine looks like it works: the parameters of *this* boot get
    baked into every image, including whatever a one-shot entry added.
    """
    if CMDLINE.is_file():
        return True
    try:
        return any(CMDLINE_D.glob("*.conf"))
    except OSError:
        return False


def _free_mb(path: Path) -> tuple[Path, int] | None:
    """Free megabytes where `path` will be written, and what was measured.

    Walked up from the image rather than aimed at it, because an ESP is mode
    0700: statvfs on /efi/EFI/Linux is a permission error for the user running
    this while statvfs on /efi itself answers -- measured on this laptop, which
    reported 696 MiB through a drwx------ mountpoint. The directory that
    answered is returned along with the number so the report names it: if the
    ESP is not mounted the walk reaches / and the free space of the root
    filesystem is a true answer to a different question.
    """
    for candidate in [path.parent, *path.parent.parents]:
        try:
            stat = os.statvfs(candidate)
        except OSError:
            continue
        return candidate, (stat.f_bavail * stat.f_frsize) // (1024 * 1024)
    return None


def configure() -> int:
    preset_dir = ROOT / "etc/mkinitcpio.d"
    presets = mkinitcpio.presets(ROOT)
    if not presets:
        print(t("uki.no_presets", path=preset_dir))
        return 1

    texts = {preset: _read(preset) for preset in presets}
    reference = next((p for p in presets if uki_passes(texts[p])), None)
    if reference is None:
        print(t("uki.no_reference", path=preset_dir))
        return 1

    wanted = uki_passes(texts[reference])
    print(t("uki.reference", preset=reference.stem, passes=" ".join(wanted)))

    plan: dict[Path, list[str]] = {}
    left: dict[Path, list[str]] = {}
    for preset in presets:
        lagging = image_passes(texts[preset])
        if todo := [name for name in lagging if name in wanted]:
            plan[preset] = todo
        if others := [name for name in lagging if name not in wanted]:
            left[preset] = others

    # Said before the exit below as well: "nothing to do" while a pass is
    # still writing a plain image would be a narrower claim than it sounds.
    for preset, names in left.items():
        print(t("uki.left_alone", preset=preset.stem, names=" ".join(names),
                reference=reference.stem))

    if not plan:
        print(t("uki.already", preset=reference.stem))
        return 0

    if not _cmdline_present():
        print(t("uki.no_cmdline", cmdline=CMDLINE, cmdline_d=CMDLINE_D))
        return 1

    rewritten: dict[Path, str] = {}
    produced: list[Path] = []
    for preset, todo in plan.items():
        new = mkinitcpio.set_uki_output(texts[preset], todo)
        for name in todo:
            # What was written is not what is in force: the regex silently
            # matches nothing on a preset that carries no commented _uki line.
            target = mkinitcpio.preset_value(new, f"{name}_uki")
            if target is None:
                print(t("uki.no_uki_line", preset=preset, line=f"{name}_uki"))
                return 1
            print(t("uki.plan", preset=preset.stem, name=name, uki=target,
                    image=mkinitcpio.preset_value(texts[preset], f"{name}_image")))
            produced.append(Path(target))
        rewritten[preset] = new

    if space := _free_mb(produced[0]):
        print(t("uki.space", path=space[0], free=space[1]))
    else:
        print(t("uki.space_unknown", path=produced[0].parent))

    if not ask_yes(t("uki.continue_q")):
        print(t("msg.cancelled"))
        return 0

    stamp = date.today().isoformat()
    rc = 0
    backups: list[tuple[Path, Path]] = []
    for preset, new in rewritten.items():
        backup = preset.with_name(f"{preset.name}.yedek-{stamp}")
        code, changed = sysedit.write_with_backup(preset, new, backup=backup)
        rc |= code
        if changed:
            backups.append((preset, backup))

    def report_undo() -> None:
        for written, backup in backups:
            print(t("uki.undo", backup=backup, preset=written))

    # A write that fails partway leaves the presets that did land rewritten,
    # so the way back is printed on this exit too rather than only after a
    # clean run -- the exit where it is needed most is the one that failed.
    if rc != 0 or not backups:
        report_undo()
        return rc

    # Rebuilds every preset, not only the rewritten ones, and verifies the
    # signatures afterwards -- one door, so the check cannot be left out.
    rc |= mkinitcpio.regenerate(ROOT)

    present = mkinitcpio.sizes(produced)
    if not present:
        # Nothing could be measured at all, which is a statement about the
        # measurement and not about the images; saying "missing" here would
        # report a failure that was never established.
        print(t("uki.unverified", paths=" ".join(str(p) for p in produced)))
    else:
        for path in produced:
            size = present.get(path)
            if size is None:
                print(t("uki.missing", path=path))
                rc |= 1
            else:
                print(t("uki.produced", path=path, mib=round(size / 1024**2, 1)))

    print(t("uki.default_shifts"))
    report_undo()
    return rc
