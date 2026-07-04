"""Installer (live ISO) mode: disk, pacstrap prep, chroot config, bootloaders."""

import re

import pytest

from archsetup.installer import base, bootloaders, chroot, disk
from archsetup.installer.state import state


@pytest.fixture(autouse=True)
def unsafe(monkeypatch):
    monkeypatch.setenv("ARCHSETUP_UNSAFE", "1")
    yield
    state.bootdev = state.swapdev = state.rootdev = state.homedev = None
    state.fs_packages.clear()


def _feed(monkeypatch, module, answers):
    answers_iter = iter(answers)
    monkeypatch.setattr(module, "input", lambda prompt="": next(answers_iter), raising=False)


def test_btrfs_root_creates_subvolume(monkeypatch, runlog):
    monkeypatch.setattr(disk, "run", runlog)
    _feed(monkeypatch, disk, ["1"])  # btrfs
    assert disk._format_one("root", "/dev/sda2", disk.ROOT_FS) == 0
    assert ["mkfs.btrfs", "-L", "root", "-f", "/dev/sda2"] in runlog.calls
    assert ["btrfs", "subvolume", "set-default", "/mnt/root"] in runlog.calls
    assert "btrfs-progs" in state.fs_packages


def test_esp_forced_to_fat32(monkeypatch, runlog):
    monkeypatch.setattr(disk, "run", runlog)
    _feed(monkeypatch, disk, ["1"])  # fat32
    disk._format_one("boot", "/dev/sda1", disk.BOOT_FS)
    assert ["mkfs.fat", "-F", "32", "-n", "BOOT", "/dev/sda1"] in runlog.calls


def test_guard_refuses_outside_iso(monkeypatch):
    monkeypatch.delenv("ARCHSETUP_UNSAFE")
    monkeypatch.setattr(disk.env, "is_archiso", lambda: False)
    assert disk.guard() is False
    assert disk.format_devices() == 1
    assert disk.mount_all() == 1


def test_enable_multilib(tmp_path, monkeypatch, runlog):
    (tmp_path / "etc").mkdir()
    conf = tmp_path / "etc" / "pacman.conf"
    conf.write_text(
        "[options]\n#Color\n\n#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n\n[core]\n"
    )
    monkeypatch.setattr(base, "MNT", tmp_path)
    monkeypatch.setattr(base, "run", runlog)

    assert base.enable_multilib() == 0
    text = conf.read_text()
    assert "\n[multilib]\nInclude = /etc/pacman.d/mirrorlist\n" in text
    assert "#Color" in text
    base.enable_multilib()
    assert conf.read_text().count("[multilib]") == 1


def test_uki_preset_conversion(tmp_path, monkeypatch, runlog):
    etc = tmp_path / "etc"
    (etc / "mkinitcpio.d").mkdir(parents=True)
    (tmp_path / "usr").mkdir()
    preset = etc / "mkinitcpio.d" / "linux-zen.preset"
    preset.write_text(
        'ALL_kver="/boot/vmlinuz-linux-zen"\n'
        '#ALL_config="/etc/mkinitcpio.conf"\n'
        "PRESETS=('default' 'fallback')\n"
        'default_image="/boot/initramfs-linux-zen.img"\n'
        '#default_uki="/efi/EFI/Linux/arch-linux-zen.efi"\n'
        '#default_options="--splash x.bmp"\n'
    )
    (etc / "mkinitcpio.conf").write_text("HOOKS=(base udev block filesystems fsck)\n")
    chroot_calls = []
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "chroot_run", lambda a: chroot_calls.append(a) or 0)

    assert chroot.gen_uki() == 0
    text = preset.read_text()
    assert 'default_uki="/efi/EFI/Linux/arch-linux-zen.efi"' in text
    assert "#default_image=" in text and "PRESETS=('default')" in text
    assert "base systemd" in (etc / "mkinitcpio.conf").read_text()
    assert ["mkdir", "-p", "/efi/EFI/Linux"] in chroot_calls
    assert ["mkinitcpio", "-P"] in chroot_calls


def test_watchdog_amd(tmp_path, monkeypatch):
    etc = tmp_path / "etc"
    (etc / "kernel").mkdir(parents=True)
    cmdline = etc / "kernel" / "cmdline"
    cmdline.write_text("root=PARTUUID=x rw quiet\n")
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot.hardware, "cpu_matches", lambda q: q == "amd")

    assert chroot.disable_watchdog() == 0
    assert cmdline.read_text().strip().endswith("nowatchdog")
    assert "sp5100_tco" in (etc / "modprobe.d" / "blacklist-watchdog.conf").read_text()
    chroot.disable_watchdog()
    assert cmdline.read_text().count("nowatchdog") == 1


USERNAME_RE = r"[a-z_][a-z0-9_-]{2,31}"


@pytest.mark.parametrize("name,ok", [
    ("drpars", True), ("_svc", True), ("ab", False), ("1bad", False),
    ("Upper", False), ("has space", False),
])
def test_username_validation(name, ok):
    assert bool(re.fullmatch(USERNAME_RE, name)) is ok


@pytest.fixture
def boot_env(tmp_path, monkeypatch, runlog):
    (tmp_path / "etc").mkdir()
    monkeypatch.setattr(bootloaders, "MNT", tmp_path)
    monkeypatch.setattr(bootloaders, "run", runlog)
    monkeypatch.setattr(bootloaders, "chroot_run", lambda a: 0)
    monkeypatch.setattr(bootloaders, "target_ready", lambda: True)
    monkeypatch.setattr(
        bootloaders, "_blkid",
        lambda dev, tag: {"PARTUUID": "abcd-1234", "TYPE": "ext4"}[tag],
    )
    state.rootdev = "/dev/sda2"
    return tmp_path


def test_systemd_boot_install(boot_env, monkeypatch):
    monkeypatch.setattr(bootloaders.disk, "is_efi", lambda: True)
    assert bootloaders.install_systemd_boot() == 0
    cmdline = (boot_env / "etc" / "kernel" / "cmdline").read_text().strip()
    assert cmdline == (
        "root=PARTUUID=abcd-1234 quiet rw rootfstype=ext4 systemd.unit=graphical.target"
    )
    assert (boot_env / "efi/loader/loader.conf").exists()
    assert (boot_env / "etc/pacman.d/hooks/95-systemd-boot.hook").exists()


def test_efi_only_bootloaders_rejected_on_bios(boot_env, monkeypatch):
    monkeypatch.setattr(bootloaders.disk, "is_efi", lambda: False)
    assert bootloaders.install_systemd_boot() == 1
    assert bootloaders.install_refind() == 1


def test_refind_conf(boot_env, monkeypatch):
    monkeypatch.setattr(bootloaders.disk, "is_efi", lambda: True)
    (boot_env / "boot").mkdir()
    assert bootloaders.install_refind() == 0
    text = (boot_env / "boot" / "refind_linux.conf").read_text()
    assert '"Boot with standard options" "root=PARTUUID=abcd-1234' in text
    assert "single" in text
