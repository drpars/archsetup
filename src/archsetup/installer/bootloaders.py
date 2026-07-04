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
from .chroot import chroot_run, target_ready
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
    if disk.is_efi():
        return True
    print(t("inst.efi_required"))
    return False


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
    (MNT / "efi/loader/loader.conf").write_text(
        "timeout  menu-force\nconsole-mode  max\n", encoding="utf-8"
    )
    hooks_dir = MNT / "etc/pacman.d/hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "95-systemd-boot.hook").write_text(SDBOOT_HOOK, encoding="utf-8")
    (MNT / "etc/kernel/cmdline").write_text(cmdline + "\n", encoding="utf-8")
    print(f"/mnt/etc/kernel/cmdline <- {cmdline}")

    if subprocess.run(["lspci"], capture_output=True, text=True).stdout.find(
        "VirtualBox G"
    ) != -1:
        (MNT / "efi/startup.nsh").write_text(
            "\\EFI\\systemd\\systemd-bootx64.efi\n", encoding="utf-8"
        )

    print(t("inst.sdboot_done"))
    return rc


def install_grub() -> int:
    if not target_ready():
        return 1
    cmdline = _root_cmdline()
    if cmdline is None:
        return 1

    if disk.is_efi():
        rc = run(["pacstrap", str(MNT), "grub", "efibootmgr"])
        rc |= chroot_run([
            "grub-install", "--target=x86_64-efi",
            "--efi-directory=/efi", "--bootloader-id=GRUB",
        ])
    else:
        disks = disk.list_devices("disk")
        target = disk._choose(t("inst.pick_disk"), disks)
        if not target:
            return 1
        rc = run(["pacstrap", str(MNT), "grub"])
        rc |= chroot_run(["grub-install", "--target=i386-pc", target])

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
