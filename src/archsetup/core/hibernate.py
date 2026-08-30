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

configure() works off whatever swap is active, not off /swapfile: this
installer offers a swap *partition* too (disk.select_partitions ->
state.swapdev), and a machine that took it used to be told "no swapfile"
by the one task that exists to make it hibernate. resize() and remove()
stay about /swapfile, which is the file this tool creates.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
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

# The kernel's own two words for what a swap area is, not a guess: /proc/swaps
# prints S_ISBLK(file_inode(file)->i_mode) ? "partition" : "file" in
# mm/swapfile.c swap_show(), and swapon --show passes the column through.
FILE = "file"
PARTITION = "partition"


@dataclass(frozen=True)
class SwapArea:
    """One active swap area: its path, the kernel's word for it, its size."""

    name: str
    kind: str
    size: int


def _swap_areas() -> list[SwapArea]:
    """Every active swap area, read off swapon rather than assumed.

    The columns are read from the right, because NAME is the only one
    that could ever hold something unexpected: the kernel escapes
    whitespace in the path when it prints /proc/swaps, so splitting from
    the left is safe today and this stays safe if that ever changes.
    """
    out = subprocess.run(
        ["swapon", "--show=NAME,TYPE,SIZE", "--bytes", "--noheadings"],
        capture_output=True,
        text=True,
    )
    areas: list[SwapArea] = []
    for line in out.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3 or not fields[-1].isdigit():
            continue
        areas.append(SwapArea(" ".join(fields[:-2]), fields[-2], int(fields[-1])))
    return areas


def _resume_area() -> SwapArea | None:
    """The one swap area resume= will name, or None with the reason printed.

    The image does not go wherever the kernel finds room, which is what an
    earlier version of this file assumed. swsusp_swap_check() resolves
    resume=/resume_offset= to a single swap type -- root_swap -- and every
    page is written through alloc_swapdev_block(root_swap); enough_swap()
    likewise counts only count_swap_pages(root_swap, 1) (kernel/power/swap.c,
    read against the tree this laptop runs). So the question is which single
    area, not how much swap there is in total.

    /swapfile wins when several are active because it is the one this tool
    creates and sizes from RAM. With several and none of them ours the choice
    belongs to whoever set the machine up, and this says so rather than
    picking one -- naming the wrong device is the failure this task exists to
    avoid.
    """
    areas = _swap_areas()
    if not areas:
        print(t("msg.swap_none_active"))
        return None
    if len(areas) == 1:
        return areas[0]
    own = [area for area in areas if area.name == SWAPFILE]
    if own:
        return own[0]
    print(t("msg.swap_many_areas", areas=" ".join(area.name for area in areas)))
    return None


def _swapfile_active() -> bool:
    return any(area.name == SWAPFILE for area in _swap_areas())


def _swap_uuid(path: str) -> str:
    """The UUID of the filesystem holding a swapfile.

    Not the swap area's own UUID: for a regular file the kernel records
    si->bdev = inode->i_sb->s_bdev, so the device resume= has to name is the
    one the file lives on. A block device carries its own -- see _area_uuid().
    """
    out = subprocess.run(
        ["findmnt", "-no", "UUID", "-T", path], capture_output=True, text=True
    )
    return out.stdout.strip()


def _area_uuid(device: str) -> str:
    """The swap signature's UUID on a swap partition.

    lsblk rather than blkid or `swapon --show=UUID`: it answers from the udev
    database instead of probing, so it works as an ordinary user. Measured
    here -- lsblk -no UUID returned the UUID of every partition on this disk,
    while swapon --show=UUID came back empty for the active swapfile.
    """
    out = subprocess.run(
        ["lsblk", "-no", "UUID", device], capture_output=True, text=True
    )
    return out.stdout.strip()


def _swap_fstype(path: str) -> str:
    out = subprocess.run(
        ["findmnt", "-no", "FSTYPE", "-T", path], capture_output=True, text=True
    )
    return out.stdout.strip()


def _swap_offset(path: str) -> str:
    """The offset resume needs, which is not the same number on every filesystem.

    filefrag reports the first extent's physical offset and on ext4 that is
    exactly what resume_offset wants -- measured on this laptop, 309248. On
    btrfs it reports a filesystem-logical address instead, and the two
    disagree: measured on a loopback btrfs holding one NOCOW swapfile,
    filefrag said 86880 where `btrfs inspect-internal map-swapfile -r` said
    115136. Measured a second time on a real install rather than a loopback
    (QEMU, 2026-08-31, btrfs root with this tool's own NOCOW swapfile):
    filefrag 859392, map-swapfile 926976. Different numbers, same
    disagreement -- and that install then hibernated and resumed on the
    offset this branch returns.

    Neither number looks wrong from here, which is why this branches rather
    than picking the tool that works on the machine it was written on.
    Writing filefrag's answer on btrfs puts a plausible and incorrect
    resume_offset on the cmdline, and nothing reports it until a resume that
    does not come back.

    map-swapfile also declines a file it would be wrong about ("file is not
    NOCOW"), and that non-numeric output falls through to the empty return
    that configure() already treats as a failure.
    """
    if _swap_fstype(path) == BTRFS:
        out = subprocess.run(
            ["sudo", "btrfs", "inspect-internal", "map-swapfile", "-r", path],
            capture_output=True,
            text=True,
        )
        value = out.stdout.strip()
        return value if value.isdigit() else ""

    out = subprocess.run(
        ["sudo", "filefrag", "-v", path], capture_output=True, text=True
    )
    for line in out.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == "0:":
            return fields[3].rstrip(".:")
    return ""


def _ensure_offset_tool(path: str) -> bool:
    """Install whichever package answers the offset question here."""
    if _swap_fstype(path) == BTRFS:
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


def _image_size() -> int:
    try:
        return int(IMAGE_SIZE.read_text())
    except (OSError, ValueError):
        return 0


def _swap_is_big_enough(area: SwapArea) -> bool:
    """Ask, on a measured negative, before promising hibernation.

    /sys/power/image_size is the kernel's own target for the image, 2/5 of
    RAM by default (measured here: 0.396). Swap smaller than that number
    cannot hold even the image the kernel is aiming to produce -- and the
    failure is not a refusal at hibernate time. Measured on this laptop:
    swap 8.0 GiB against a 10.76 GiB target, systemd's own precheck passed
    because memory happened to be mostly free, the kernel entered
    hibernation, and the machine cold-booted 2.5 minutes later.

    The number compared is one area's size, not the sum of every active one.
    That used to be a sum, on the belief that the image goes wherever there
    is room; the kernel writes it to root_swap alone and asks
    count_swap_pages(root_swap, 1) whether it fits, so a small resume target
    next to a large second area was being called adequate.

    A question rather than a refusal: a machine kept deliberately lean can
    still hibernate, and the caller may only want the resume parameters
    written. Non-interactive runs read as "no", which is the safe direction
    for a task whose failure mode is a lost session.
    """
    swap, image = area.size, _image_size()
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


def _resume_params(area: SwapArea) -> list[str] | None:
    """resume= for the live area -- plus an offset only a file needs.

    A swap partition takes no resume_offset=, and must not be left carrying
    one. The kernel matches an area by
    `device == sis->bdev->bd_dev && first_se(sis)->start_block == offset`
    (__find_hibernation_swap_type), and a block device's only extent is
    add_swap_extent(sis, 0, sis->max, 0) -- start_block 0. So an offset left
    over from a swapfile makes the lookup return -ENODEV and hibernation
    refuse outright.

    Nothing writes resume_offset=0 to say so: add_kernel_params() is called
    with replace_prefixes=RESUME_PREFIXES, which drops every matching token
    that is not one of the new params, and an absent resume_offset= is 0
    anyway (swsusp_resume_block is a zero-initialised global).
    """
    if area.kind == PARTITION:
        uuid = _area_uuid(area.name)
        if not uuid:
            print(t("msg.swap_params_failed"))
            return None
        return [f"resume=UUID={uuid}"]

    uuid = _swap_uuid(area.name)
    offset = _swap_offset(area.name)
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
    area = _resume_area()
    if area is None:
        return 1
    print(t("msg.swap_resume_target", path=area.name, kind=area.kind))

    # Only a file has an offset to derive, so only a file needs the tool
    # that derives it.
    if area.kind != PARTITION and not _ensure_offset_tool(area.name):
        return 1

    if not _swap_is_big_enough(area):
        return 0

    params = _resume_params(area)
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
    The same branch covers a machine whose swap is a partition: there is no
    file to take away and this does not touch the device, so what is being
    undone is the configuration, and the prompt says that rather than
    offering to remove a /swapfile that was never there.

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

    plan = "msg.swap_remove_plan" if exists else "msg.swap_remove_plan_params"
    print(t(plan, path=SWAPFILE))
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
        print(t("msg.swap_removed" if exists else "msg.swap_params_removed",
                path=SWAPFILE))
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
    if not _ensure_offset_tool(SWAPFILE):
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
    # The current size, not RAM: pressing enter has to be the answer that
    # changes nothing. Measured on this laptop, where swap is 28672 MiB and
    # RAM is 27807 -- a RAM default would have quietly shrunk it.
    default = str(current // 2**20)
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
    params = _resume_params(SwapArea(SWAPFILE, FILE, want))
    if params is None:
        return 1
    return _apply_resume_params(params)
