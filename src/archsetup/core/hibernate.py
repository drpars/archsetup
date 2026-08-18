"""Swap file hibernation configuration.

Ported from installarchde's swap_file_config with fixes:
- resume=UUID / resume_offset go through core.bootloader, so they land
  in the right place for UKI, systemd-boot entries, GRUB or rEFInd
  (the old code wrote /etc/kernel/cmdline unconditionally),
- stale resume parameters are replaced instead of duplicated,
- the mkinitcpio "resume" hook is only added on busybox initramfs; the
  systemd hook resumes on its own (per Arch Wiki), so it is skipped,
- mkinitcpio -P runs once at the end.

configure() writes the parameters, resize() changes the swapfile's size and
re-derives them, and remove() takes both the file and the parameters away --
the last one being the missing half of the second fix above: stale parameters
were replaced, and a swapfile that went away left them pointing at nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import bootloader, gpuconfig, hardware, i18n, mkinitcpio
from .pacman import run
from .prompt import ask_yes
from .sysedit import sudo_write

t = i18n.t

SWAPFILE = "/swapfile"
IMAGE_SIZE = Path("/sys/power/image_size")
CONF_D = Path("/etc/mkinitcpio.conf.d")
BTRFS = "btrfs"
RESUME_PREFIXES = ("resume=", "resume_offset=")


def _swapfile_active() -> bool:
    out = subprocess.run(
        ["swapon", "--show=NAME", "--noheadings"], capture_output=True, text=True
    )
    return SWAPFILE in out.stdout.split()


def _swap_uuid() -> str:
    out = subprocess.run(
        ["findmnt", "-no", "UUID", "-T", SWAPFILE], capture_output=True, text=True
    )
    return out.stdout.strip()


def _swap_fstype(path: str = SWAPFILE) -> str:
    out = subprocess.run(
        ["findmnt", "-no", "FSTYPE", "-T", path], capture_output=True, text=True
    )
    return out.stdout.strip()


def _swap_offset() -> str:
    """The offset resume needs, which is not the same number on every filesystem.

    filefrag reports the first extent's physical offset and on ext4 that is
    exactly what resume_offset wants -- measured on this laptop, 309248. On
    btrfs it reports a filesystem-logical address instead, and the two
    disagree: measured on a loopback btrfs holding one NOCOW swapfile,
    filefrag said 86880 where `btrfs inspect-internal map-swapfile -r` said
    115136.

    Neither number looks wrong from here, which is why this branches rather
    than picking the tool that works on the machine it was written on.
    Writing filefrag's answer on btrfs puts a plausible and incorrect
    resume_offset on the cmdline, and nothing reports it until a resume that
    does not come back.

    map-swapfile also declines a file it would be wrong about ("file is not
    NOCOW"), and that non-numeric output falls through to the empty return
    that configure() already treats as a failure.
    """
    if _swap_fstype() == BTRFS:
        out = subprocess.run(
            ["sudo", "btrfs", "inspect-internal", "map-swapfile", "-r", SWAPFILE],
            capture_output=True,
            text=True,
        )
        value = out.stdout.strip()
        return value if value.isdigit() else ""

    out = subprocess.run(
        ["sudo", "filefrag", "-v", SWAPFILE], capture_output=True, text=True
    )
    for line in out.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == "0:":
            return fields[3].rstrip(".:")
    return ""


def _ensure_offset_tool() -> bool:
    """Install whichever package answers the offset question here."""
    if _swap_fstype() == BTRFS:
        if shutil.which("btrfs") is None:
            return run(["sudo", "pacman", "-S", "--needed", "btrfs-progs"]) == 0
        return True
    if shutil.which("filefrag") is None:
        return run(["sudo", "pacman", "-S", "--needed", "e2fsprogs"]) == 0
    return True


def _free_bytes() -> int:
    """Free space on the filesystem holding the swapfile, or 0 if unmeasurable."""
    try:
        st = os.statvfs(Path(SWAPFILE).parent)
    except OSError:
        return 0
    return st.f_bavail * st.f_frsize


def _swap_bytes() -> int:
    """Every active swap area, not just the swapfile.

    The image goes wherever the kernel finds room, so a machine with a swap
    partition alongside the file is not short just because the file is.
    """
    out = subprocess.run(
        ["swapon", "--show=SIZE", "--bytes", "--noheadings"],
        capture_output=True,
        text=True,
    )
    total = 0
    for line in out.stdout.split():
        try:
            total += int(line)
        except ValueError:
            continue
    return total


def _image_size() -> int:
    try:
        return int(IMAGE_SIZE.read_text())
    except (OSError, ValueError):
        return 0


def _swap_is_big_enough() -> bool:
    """Ask, on a measured negative, before promising hibernation.

    /sys/power/image_size is the kernel's own target for the image, 2/5 of
    RAM by default (measured here: 0.396). Swap smaller than that number
    cannot hold even the image the kernel is aiming to produce -- and the
    failure is not a refusal at hibernate time. Measured on this laptop:
    swap 8.0 GiB against a 10.76 GiB target, systemd's own precheck passed
    because memory happened to be mostly free, the kernel entered
    hibernation, and the machine cold-booted 2.5 minutes later.

    A question rather than a refusal: a machine kept deliberately lean can
    still hibernate, and the caller may only want the resume parameters
    written. Non-interactive runs read as "no", which is the safe direction
    for a task whose failure mode is a lost session.
    """
    swap, image = _swap_bytes(), _image_size()
    if not swap or not image or swap >= image:
        return True
    print(t("msg.swap_too_small", swap=swap // 2**20, image=image // 2**20))
    if ask_yes(t("msg.swap_continue_q")):
        return True
    print(t("msg.cancelled"))
    return False


def _drop_nvidia_from_initramfs() -> bool:
    """Offer to take the NVIDIA modules out of MODULES; True if the file changed.

    A driver's .freeze callback only runs where that driver is bound, so what
    breaks resume is not early KMS in general but NVIDIA being loaded in the
    *resume* kernel -- the one the initramfs starts, where no systemd unit has
    written /proc/driver/nvidia/suspend to save VRAM. Measured on this laptop:
    the image was read back at full speed and then

        NVRM: GPU 0000:01:00.0: PreserveVideoMemoryAllocations module parameter
        is set. System Power Management attempted without driver procfs suspend
        interface.
        nvidia 0000:01:00.0: PM: pci_pm_freeze(): nv_pmops_freeze returns -5

    and the restore unwound -- which reads from outside as "hibernation just
    doesn't work". With MODULES=(amdgpu) the same machine hibernated and came
    back on the same boot id.

    This task owns the question because this task writes the resume= line that
    creates the requirement, the same rule that put xorg-xsetroot in the sddm
    task rather than in the catalogue. core.gpuconfig keeps adding the modules:
    early KMS is worth having on a machine that never hibernates, and what was
    measured is not "NVIDIA early KMS is bad".

    A question rather than a removal, and the prompt names the cost. Runs with
    no one to answer read as "no", which leaves the machine as it was.
    """
    text = gpuconfig.MKINITCPIO.read_text(encoding="utf-8")
    effective = mkinitcpio.read_array(
        mkinitcpio.effective_text(gpuconfig.MKINITCPIO, CONF_D), "MODULES"
    )
    guilty = [mod for mod in effective or [] if mod in gpuconfig.NVIDIA_MODULES]
    if not guilty:
        return False

    print(t("msg.resume_nvidia_in_initramfs", modules=" ".join(guilty)))
    if not ask_yes(t("msg.resume_nvidia_drop_q")):
        print(t("msg.resume_nvidia_kept"))
        return False

    # Only the main file is ours to edit; a drop-in belongs to whoever put it
    # there, and rewriting MODULES here would not remove what it re-adds.
    own = mkinitcpio.read_array(text, "MODULES") or []
    if not [mod for mod in own if mod in gpuconfig.NVIDIA_MODULES]:
        print(t("msg.resume_nvidia_drop_in"))
        return False

    kept = [mod for mod in own if mod not in gpuconfig.NVIDIA_MODULES]
    new_text = mkinitcpio.set_array(text, "MODULES", kept)
    return new_text is not None and sudo_write(gpuconfig.MKINITCPIO, new_text) == 0


def _ensure_resume_hook() -> bool:
    """Add the resume hook before fsck on busybox initramfs; True if changed."""
    text = gpuconfig.MKINITCPIO.read_text(encoding="utf-8")
    hooks = mkinitcpio.read_array(text, "HOOKS")
    if hooks is None:
        print(t("msg.hooks_missing"))
        return False

    if "systemd" in hooks:
        print(t("msg.resume_systemd"))
        return False
    if "resume" in hooks:
        print(t("msg.resume_hook_present"))
        return False

    if "fsck" in hooks:
        hooks.insert(hooks.index("fsck"), "resume")
    else:
        hooks.append("resume")
    new_text = mkinitcpio.set_array(text, "HOOKS", hooks)
    return new_text is not None and sudo_write(gpuconfig.MKINITCPIO, new_text) == 0


def _drop_resume_hook() -> bool:
    """Take the resume hook back out of HOOKS; True if changed.

    The mirror of _ensure_resume_hook(): a hook that waits for a resume
    device costs a boot delay once the device is gone.
    """
    text = gpuconfig.MKINITCPIO.read_text(encoding="utf-8")
    hooks = mkinitcpio.read_array(text, "HOOKS")
    if hooks is None or "resume" not in hooks:
        return False
    new_text = mkinitcpio.set_array(
        text, "HOOKS", [hook for hook in hooks if hook != "resume"]
    )
    return new_text is not None and sudo_write(gpuconfig.MKINITCPIO, new_text) == 0


def _resume_params() -> list[str] | None:
    """resume=/resume_offset= read off the live swapfile, or None."""
    uuid = _swap_uuid()
    offset = _swap_offset()
    if not uuid or not offset.isdigit():
        print(t("msg.swap_params_failed"))
        return None
    return [f"resume=UUID={uuid}", f"resume_offset={offset}"]


def _apply_resume_params(params: list[str], extra_changed: bool = False) -> int:
    """Write the parameters wherever this machine keeps them, then rebuild."""
    result = bootloader.add_kernel_params(params, replace_prefixes=RESUME_PREFIXES)
    hooks_changed = _ensure_resume_hook()

    rc = 0
    if result.needs_mkinitcpio or hooks_changed or extra_changed:
        rc = mkinitcpio.regenerate()
    if result.regen_cmd is not None:
        rc |= run(list(result.regen_cmd))

    if rc == 0:
        print(t("msg.hibernate_done", params=" ".join(params)))
    return rc


def configure() -> int:
    if not Path(SWAPFILE).is_file() or not _swapfile_active():
        print(t("msg.swapfile_missing"))
        return 1

    if not _ensure_offset_tool():
        return 1

    if not _swap_is_big_enough():
        return 0

    params = _resume_params()
    if params is None:
        return 1

    modules_changed = _drop_nvidia_from_initramfs()
    return _apply_resume_params(params, modules_changed)


def remove() -> int:
    """Take the swapfile out, and take resume= with it.

    The half this file's header was missing: "stale resume parameters are
    replaced instead of duplicated" covers a swapfile that moved, and nothing
    covered one that went away. A swapfile deleted by hand leaves
    resume=UUID=... resume_offset=... behind, and the machine then spends
    every boot waiting on a resume device that does not exist.

    The parameters go even when the file is already gone, because that is
    precisely the state a hand-deletion leaves and the one worth cleaning.

    The order is deliberate and is the opposite of how it reads: the boot
    configuration stops referring to the swapfile *before* the swapfile stops
    existing. Deleting first has a window -- and, if the delete succeeds and
    the rebuild does not, a lasting state -- where the machine boots looking
    for a file that is gone. This way the same failure costs a feature
    instead of a boot.
    """
    exists = Path(SWAPFILE).is_file()
    params_present = any(
        token.startswith(RESUME_PREFIXES)
        for token in bootloader.current_params().split()
    )
    if not exists and not params_present:
        print(t("msg.swap_nothing_to_remove", path=SWAPFILE))
        return 0

    print(t("msg.swap_remove_plan", path=SWAPFILE))
    if not ask_yes(t("msg.swap_remove_q")):
        print(t("msg.cancelled"))
        return 0

    result = bootloader.remove_kernel_params(RESUME_PREFIXES)
    hooks_changed = _drop_resume_hook()

    rc = 0
    if result.needs_mkinitcpio or hooks_changed:
        rc |= mkinitcpio.regenerate()
    if result.regen_cmd is not None:
        rc |= run(list(result.regen_cmd))
    if rc != 0:
        print(t("msg.swap_remove_boot_failed"))
        return rc

    if exists:
        if _swapfile_active():
            rc |= run(["sudo", "swapoff", SWAPFILE])
            if rc != 0:
                print(t("msg.swap_swapoff_failed", path=SWAPFILE))
                return rc
        rc |= run(["sudo", "rm", "-f", SWAPFILE])

    if rc == 0:
        print(t("msg.swap_removed", path=SWAPFILE))
    return rc


def resize() -> int:
    """Resize the swapfile, then re-derive resume_offset instead of assuming it.

    Doing this by hand is a bet: fallocate extends a file from its end, so the
    first extent is *expected* to stay where it was, and a hand-run has no
    cheap way to do better than expect. Measured here on both filesystems the
    installer offers, the expectation held -- ext4 first extent 8615 before
    and after a 64M -> 192M grow, btrfs resume offset 78272 likewise, and
    115136 unchanged across a shrink -- but those were near-empty loopback
    filesystems, which is the easy case. The tool has no reason to bet at all:
    _swap_offset() costs one command, so it runs unconditionally.

    Two of the obvious commands are silent no-ops, and neither is used the way
    a recipe would use it:

    - `fallocate -l` smaller than the file returns 0 and changes nothing
      (measured: 192M file, `fallocate -l 64M`, rc=0, size still 201326592),
      so shrinking goes through truncate.
    - `chattr +C` on a file that already holds data returns 0 and sets no flag
      (measured on btrfs: lsattr shows none, swapon then fails with EINVAL),
      so a btrfs swapfile cannot be made NOCOW after the fact. It does not
      need to be: an already-NOCOW file keeps the flag across truncate
      (measured), which is why this resizes the file in place rather than
      recreating it.
    """
    path = Path(SWAPFILE)
    if not path.is_file():
        print(t("msg.swapfile_missing"))
        return 1
    if not _ensure_offset_tool():
        return 1

    current = path.stat().st_size
    ram = hardware.ram_bytes()
    # What the filesystem can still give plus what this file already holds:
    # growing only needs the difference.
    ceiling = (current + _free_bytes()) // 2**20
    print(
        t(
            "msg.swap_resize_now",
            current=current // 2**20,
            ram=ram // 2**20,
            ceiling=ceiling,
        )
    )
    default = str((ram // 2**20) or (current // 2**20))
    raw = input(f"{t('msg.swap_resize_q')} [{default}]: ").strip() or default
    if not raw.isdigit() or int(raw) < 1:
        print(t("msg.invalid_size"))
        return 1

    size = int(raw)
    want = size * 2**20
    if want == current:
        print(t("msg.swap_resize_same", size=size))
        return 0
    if ceiling and size > ceiling:
        print(t("msg.swap_resize_too_big", want=size, ceiling=ceiling))
        return 1
    if ram and want < ram:
        print(t("msg.swap_below_ram", want=size, ram=ram // 2**20))
        if not ask_yes(t("msg.swap_below_ram_q")):
            print(t("msg.cancelled"))
            return 0

    rc = 0
    if _swapfile_active():
        rc = run(["sudo", "swapoff", SWAPFILE])
        if rc != 0:
            print(t("msg.swap_swapoff_failed", path=SWAPFILE))
            return rc

    if want > current:
        rc |= run(["sudo", "fallocate", "-l", f"{size}M", SWAPFILE])
    else:
        rc |= run(["sudo", "truncate", "-s", f"{size}M", SWAPFILE])
    rc |= run(["sudo", "mkswap", SWAPFILE])
    rc |= run(["sudo", "swapon", SWAPFILE])
    if rc != 0:
        print(t("msg.swap_resize_failed", path=SWAPFILE))
        return rc

    # The size was the easy half; this is the half a hand-run guesses at.
    params = _resume_params()
    if params is None:
        return 1
    return _apply_resume_params(params)
