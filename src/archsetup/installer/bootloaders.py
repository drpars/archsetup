"""Bootloader installation into the target: systemd-boot (UKI), GRUB, rEFInd.

systemd-boot follows the old installarch flow (loader.conf, pacman
update hook, /etc/kernel/cmdline for UKI). The VirtualBox startup.nsh is
now written to the target ESP — the old script wrote it to the live
environment's /boot, where it was lost on reboot.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..core import i18n
from ..core.pacman import run
from . import disk
from ..core.prompt import ask_yes
from .chroot import chroot_run, gen_uki, place_esp_helpers, target_ready
from .state import state

t = i18n.t

MNT = Path("/mnt")

SDBOOT_HOOK = """[Trigger]
Type = Package
Operation = Upgrade
Target = systemd

[Action]
Description = Gracefully upgrading systemd-boot...
When = PostTransaction
Exec = /usr/bin/systemctl restart systemd-boot-update.service
"""


def _blkid(device: str, tag: str) -> str:
    out = subprocess.run(
        ["blkid", "-s", tag, "-o", "value", device], capture_output=True, text=True
    )
    return out.stdout.strip()


def _root_cmdline() -> str | None:
    if state.rootdev is None:
        print(t("inst.no_selection"))
        return None
    partuuid = _blkid(state.rootdev, "PARTUUID")
    fstype = _blkid(state.rootdev, "TYPE")
    if not partuuid or not fstype:
        print(t("inst.blkid_failed", dev=state.rootdev))
        return None
    return (
        f"root=PARTUUID={partuuid} quiet rw rootfstype={fstype} "
        "systemd.unit=graphical.target"
    )


def _require_efi() -> bool:
    if not disk.is_efi():
        print(t("inst.efi_required"))
        return False
    # Second chance to catch a mistyped ESP: the selection step already
    # offered the fix, but the bootloader may be installed in a later
    # session where that step never ran.
    if state.bootdev and disk.ensure_esp_type(state.bootdev) != 0:
        return False
    return True


MENU_TIMEOUT = "3"


def _loader_conf() -> str:
    """loader.conf, with a timeout that lets the machine boot on its own.

    This used to be written as `timeout menu-force`, which is not a long
    timeout -- it is no timeout at all. systemd-boot shows the menu and waits
    for a keypress forever. Anything without someone at the keyboard (a
    server, a VM, a machine rebooted over SSH) simply never comes back up,
    and from the outside that is indistinguishable from a failed boot.

    A numeric timeout still shows the menu, so the fallback entry stays one
    keypress away; it just does not require the keypress. menu-force remains
    available for anyone who wants to choose every time.
    """
    if ask_yes(t("inst.menu_force_q")):
        timeout = "menu-force"
    else:
        timeout = MENU_TIMEOUT
    return f"timeout  {timeout}\nconsole-mode  max\n"


# `bootctl install` never puts a *new* entry first. Read in systemd v261
# (src/bootctl/bootctl-install.c, insert_into_order): the slot goes to
# `order[n]` when the operation is INSTALL_NEW, and to `order[0]` only when
# it is not -- so the very first install on a machine lands behind whatever
# the firmware already had. Reproduced twice in QEMU on 2026-08-30, same
# command, same ESP, same live ISO: run one appended the entry after four
# PXE/HTTP entries and the EFI shell, run two (the entry now already in the
# order) moved it to the front. The rig booted to PXE after run one, with
# nothing on screen to say why.
#
# systemd exposes no switch for this: `after_slot` exists in that function
# but only so the fallback lands next to the primary, and bootctl.c parses
# no option for it. So the move is ours to make, and efibootmgr is the tool
# for it -- measured present in the ISO's own package list (efibootmgr 18-4
# in /run/archiso/bootmnt/arch/pkglist.x86_64.txt), so the live environment
# always has it.
#
# The policy is not invented here: `bootctl update` moves its entry to the
# front itself. This applies that same policy to the first install, which is
# the one case systemd leaves at the back.
LOADER = "\\EFI\\systemd\\systemd-bootx64.efi"
# Named rather than inlined so the suite can seal it: this function reads
# firmware state and, one branch later, writes it. A test that sets
# state.bootdev and installs would otherwise reorder the boot entries of
# the machine running the suite.
EFIBOOTMGR = "efibootmgr"


def _boot_entries() -> tuple[list[str], dict[str, str]]:
    """(BootOrder, {slot: efibootmgr -v line}), empty when it cannot be read."""
    try:
        out = subprocess.run([EFIBOOTMGR, "-v"], capture_output=True, text=True)
    except OSError:
        return [], {}
    if out.returncode != 0:
        return [], {}
    order: list[str] = []
    entries: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if line.startswith("BootOrder:"):
            order = [s.strip() for s in line.split(":", 1)[1].split(",") if s.strip()]
        elif line.startswith("Boot") and len(line) > 8 and line[4:8].isalnum():
            entries[line[4:8]] = line
    return order, entries


def promote_boot_entry(esp_device: str) -> int:
    """Move this ESP's systemd-boot entry to the front of BootOrder.

    Never fails the install: a firmware that will not answer, a missing
    entry or an unreadable order are all reported and stepped over. The
    bootloader is installed either way; what is lost is the ordering, and
    saying so is more use than a non-zero exit from the last step.
    """
    partuuid = _blkid(esp_device, "PARTUUID")
    if not partuuid:
        print(t("inst.order_no_partuuid", dev=esp_device))
        return 0

    order, entries = _boot_entries()
    if not order:
        print(t("inst.order_unreadable"))
        return 0

    # Both the PARTUUID and the loader path, because the fallback entry
    # carries the same PARTUUID and must not be the one promoted.
    slot = next(
        (
            s for s in order
            if partuuid.lower() in entries.get(s, "").lower()
            and LOADER.lower() in entries.get(s, "").lower()
        ),
        None,
    )
    if slot is None:
        print(t("inst.order_not_found", dev=esp_device))
        return 0
    if order[0] == slot:
        print(t("inst.order_already_first", slot=slot))
        return 0

    new_order = [slot] + [s for s in order if s != slot]
    print(t("inst.order_before", order=",".join(order)))
    rc = run([EFIBOOTMGR, "-o", ",".join(new_order)])
    if rc != 0:
        print(t("inst.order_failed", rc=rc))
        return 0
    print(t("inst.order_after", order=",".join(new_order)))
    return 0


def install_systemd_boot() -> int:
    if not target_ready() or not _require_efi():
        return 1
    cmdline = _root_cmdline()
    if cmdline is None:
        return 1

    rc = run(["pacstrap", str(MNT), "efibootmgr"])
    rc |= run([
        "bootctl", f"--esp-path={MNT}/efi",
        "--efi-boot-option-description=Arch Linux", "install",
    ])

    (MNT / "efi/loader").mkdir(parents=True, exist_ok=True)
    (MNT / "efi/loader/loader.conf").write_text(_loader_conf(), encoding="utf-8")
    hooks_dir = MNT / "etc/pacman.d/hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "95-systemd-boot.hook").write_text(SDBOOT_HOOK, encoding="utf-8")
    (MNT / "etc/kernel").mkdir(parents=True, exist_ok=True)
    (MNT / "etc/kernel/cmdline").write_text(cmdline + "\n", encoding="utf-8")
    print(f"/mnt/etc/kernel/cmdline <- {cmdline}")

    if subprocess.run(["lspci"], capture_output=True, text=True).stdout.find(
        "VirtualBox G"
    ) != -1:
        (MNT / "efi/startup.nsh").write_text(
            "\\EFI\\systemd\\systemd-bootx64.efi\n", encoding="utf-8"
        )

    if rc != 0:
        return rc

    if state.bootdev:
        promote_boot_entry(state.bootdev)

    # Helpers go on now that loader.conf exists: the memtest entry is only
    # written where there is a systemd-boot menu to put it in, and this is
    # the step that creates one. Extra packages are installed before the
    # target menu opens, so anything selected there is already in place.
    place_esp_helpers()

    # Apart from that entry this flow writes no /efi/loader/entries, so the
    # only thing systemd-boot can boot is a UKI. Skipping the step leaves an
    # installed system with nothing but a memory tester in its boot menu —
    # hence the prompt here instead of a separate menu item the user has to
    # remember.
    print(t("inst.sdboot_done"))
    if ask_yes(t("inst.uki_now_q")):
        rc |= gen_uki()
    return rc


# GPT type GUID of a BIOS boot partition, as lsblk reports PARTTYPE.
# Measured 2026-08-30 in the guest against a loop device partitioned with
# `sgdisk -t 1:ef02`: 21686148-... with PARTTYPENAME "BIOS boot".
BIOS_BOOT_GUID = "21686148-6449-6e6f-744e-656564454649"


def _gpt_without_bios_boot(target: str) -> bool:
    """True when `target` is GPT and carries no BIOS boot partition.

    That combination is the one `grub-install --target=i386-pc` refuses:
    with no gap to embed core.img into it falls back to blocklists and
    then declines to use them. On an MBR label there is a gap after the
    boot record and the question does not arise, so the check is narrowed
    to GPT rather than asked of every disk.
    """
    out = subprocess.run(
        ["lsblk", "-n", "-o", "PTTYPE,PARTTYPE", target],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return False  # unreadable is not a finding; grub-install still speaks
    lines = out.stdout.splitlines()
    if not lines or lines[0].split()[0:1] != ["gpt"]:
        return False
    return BIOS_BOOT_GUID not in out.stdout.lower()


def install_grub() -> int:
    if not target_ready():
        return 1
    cmdline = _root_cmdline()
    if cmdline is None:
        return 1

    if disk.is_efi():
        # No promote_boot_entry() here, and that is measured rather than
        # assumed: grub-install creates its entry through `efibootmgr -c`,
        # which prepends. Measured in QEMU/OVMF on 2026-08-30 against the
        # same nine firmware entries (four of them PXE/HTTP) that push a
        # fresh `bootctl install` to the back -- GRUB came out at the head
        # of BootOrder, a second run added no duplicate, and a machine
        # booted with the ISO still attached and no -boot d reported
        # BootCurrent: 0009. The ordering problem is bootctl's, not the
        # firmware's. Not measured on real firmware, and not measured for
        # refind-install.
        rc = run(["pacstrap", str(MNT), "grub", "efibootmgr"])
        rc |= chroot_run([
            "grub-install", "--target=x86_64-efi",
            "--efi-directory=/efi", "--bootloader-id=GRUB",
        ])
    else:
        disks = disk.list_devices("disk")
        target = disk._choose(t("inst.grub_pick_disk"), disks)
        if not target:
            return 1
        # Asked before the install rather than after it fails, because the
        # failure is a wall of grub-install warnings ending in "will not
        # proceed with blocklists" and the fix -- a 1 MiB ef02 partition --
        # is nowhere in it. Measured in QEMU: a GPT disk laid out the way
        # this repo's own README describes (which is the UEFI layout) hits
        # exactly this, and the layout is what the user was told to make.
        if _gpt_without_bios_boot(target):
            print(t("inst.grub_needs_bios_boot", dev=target))
            return 1
        rc = run(["pacstrap", str(MNT), "grub"])
        rc |= chroot_run(["grub-install", "--target=i386-pc", target])

    # Stop here when grub-install failed. It used to fall through to
    # grub-mkconfig, which succeeds on its own and prints "done" -- so a
    # disk with no boot code in it ended the step on the word done, with
    # the real error scrolled off the top. Measured in the BIOS run.
    if rc != 0:
        print(t("inst.grub_install_failed", rc=rc))
        return rc

    grub_default = MNT / "etc/default/grub"
    text = grub_default.read_text(encoding="utf-8")
    # rootfstype/root= come from grub-mkconfig; carry over the rest.
    extra = "quiet systemd.unit=graphical.target"
    new_text = text.replace(
        'GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"',
        f'GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 {extra}"',
        1,
    )
    grub_default.write_text(new_text, encoding="utf-8")

    rc |= chroot_run(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"])
    return rc


def install_refind() -> int:
    if not target_ready() or not _require_efi():
        return 1
    cmdline = _root_cmdline()
    if cmdline is None:
        return 1

    rc = run(["pacstrap", str(MNT), "refind"])
    rc |= chroot_run(["refind-install"])
    (MNT / "boot/refind_linux.conf").write_text(
        f'"Boot with standard options" "{cmdline}"\n'
        f'"Boot to single-user mode" "{cmdline} single"\n',
        encoding="utf-8",
    )
    return rc
