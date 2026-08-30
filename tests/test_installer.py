"""Installer (live ISO) mode: disk, pacstrap prep, chroot config, bootloaders."""

import ast
import re
from pathlib import Path

import pytest

from archsetup.core import hardware, i18n, repos, writeback
from archsetup.installer import (
    base,
    blockdev,
    bootloaders,
    chroot,
    disk,
    erase,
    nvme,
    pickers,
)
from archsetup.installer.state import state


@pytest.fixture(autouse=True)
def unsafe(monkeypatch):
    monkeypatch.setenv("ARCHSETUP_UNSAFE", "1")
    yield
    state.bootdev = state.swapdev = state.rootdev = state.homedev = None
    state.fs_packages.clear()
    state.parallel_downloads = None


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


def test_esp_mounted_with_a_restrictive_mask(tmp_path, monkeypatch, runlog):
    """fstab comes from `genfstab -p`, which copies the live mount options.

    Getting this wrong leaves the ESP world readable and bootctl warning that
    /efi/loader/random-seed is world accessible. It cannot be repaired with a
    remount either: vfat reads fmask/dmask only on the first mount.
    """
    monkeypatch.setattr(disk, "run", runlog)
    monkeypatch.setattr(disk, "MNT", tmp_path)
    monkeypatch.setattr(disk, "_lsblk_value", lambda dev, column: "vfat")
    state.rootdev, state.bootdev = "/dev/sda2", "/dev/sda1"

    assert disk.mount_all() == 0
    assert [
        "mount", "-o", "fmask=0077,dmask=0077", "/dev/sda1", f"{tmp_path}/efi"
    ] in runlog.calls


def test_non_vfat_boot_partition_gets_no_mask(tmp_path, monkeypatch, runlog):
    """BOOT_FS also allows ext2/3/4, which reject fmask/dmask outright."""
    monkeypatch.setattr(disk, "run", runlog)
    monkeypatch.setattr(disk, "MNT", tmp_path)
    monkeypatch.setattr(disk, "_lsblk_value", lambda dev, column: "ext4")
    state.rootdev, state.bootdev = "/dev/sda2", "/dev/sda1"

    assert disk.mount_all() == 0
    assert ["mount", "/dev/sda1", f"{tmp_path}/efi"] in runlog.calls


def _swaps_file(tmp_path, *entries) -> Path:
    path = tmp_path / "swaps"
    header = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
    body = "".join(f"{name}\t\t\t\tfile\t\t3993596\t\t0\t\t-2\n" for name in entries)
    path.write_text(header + body, encoding="utf-8")
    return path


def test_unmount_swaps_off_our_own_swapfile_first(tmp_path, monkeypatch, runlog):
    """A swapfile inside /mnt holds the mount: `umount -R` gets EBUSY.

    Measured in QEMU 2026-08-30, on the path the tool itself builds --
    the swapfile step then the finish step: `fuser -vm /mnt` reported
    `kernel swap /mnt/swapfile`, and umount failed with "target is busy".
    """
    monkeypatch.setattr(disk, "run", runlog)
    monkeypatch.setattr(disk, "MNT", tmp_path)
    monkeypatch.setattr(disk, "SWAPS", _swaps_file(tmp_path, f"{tmp_path}/swapfile"))
    state.swapdev = None

    assert disk.unmount_all() == 0
    swapoff = ["swapoff", f"{tmp_path}/swapfile"]
    assert swapoff in runlog.calls
    # Order is the whole point: a swapoff after the umount frees the mount
    # only once the thing it was blocking has already failed.
    assert runlog.calls.index(swapoff) < runlog.calls.index(
        ["umount", "-R", str(tmp_path)]
    )


def test_unmount_still_swaps_off_the_chosen_partition(tmp_path, monkeypatch, runlog):
    """A swap partition is /dev/... in /proc/swaps, so the prefix misses it."""
    monkeypatch.setattr(disk, "run", runlog)
    monkeypatch.setattr(disk, "MNT", tmp_path)
    monkeypatch.setattr(disk, "SWAPS", _swaps_file(tmp_path, "/dev/sda3"))
    state.swapdev = "/dev/sda3"

    assert disk.unmount_all() == 0
    assert ["swapoff", "/dev/sda3"] in runlog.calls
    assert [call for call in runlog.calls if call[0] == "swapoff"] == [
        ["swapoff", "/dev/sda3"]
    ]


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
        'fallback_image="/boot/initramfs-linux-zen-fallback.img"\n'
        '#fallback_uki="/efi/EFI/Linux/arch-linux-zen-fallback.efi"\n'
        '#fallback_options="-S autodetect"\n'
    )
    (etc / "mkinitcpio.conf").write_text("HOOKS=(base udev block filesystems fsck)\n")
    chroot_calls = []
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "chroot_run", lambda a: chroot_calls.append(a) or 0)

    # cmdline yoksa reddedilir (UKI root'u bulamaz)
    assert chroot.gen_uki() == 1
    (etc / "kernel").mkdir()
    (etc / "kernel" / "cmdline").write_text("root=PARTUUID=x rw\n")

    assert chroot.gen_uki() == 0
    text = preset.read_text()
    assert 'default_uki="/efi/EFI/Linux/arch-linux-zen.efi"' in text
    assert "#default_image=" in text
    assert "base systemd" in (etc / "mkinitcpio.conf").read_text()
    assert ["mkdir", "-p", "/efi/EFI/Linux"] in chroot_calls
    assert ["mkinitcpio", "-P"] in chroot_calls
    # Fallback korunur: bozuk bir imaj üretilirse dönülecek tek yer o.
    assert "PRESETS=('default' 'fallback')" in text
    assert 'fallback_uki="/efi/EFI/Linux/arch-linux-zen-fallback.efi"' in text
    assert "#fallback_image=" in text
    # -S autodetect: fallback her modülü taşır, yalnızca o anki donanımı değil.
    assert 'fallback_options="-S autodetect"' in text


def _presets_lines(text):
    return [l for l in text.splitlines() if l.startswith("PRESETS=")]


def test_fallback_preset_survives_the_default_only_layout():
    """Some presets ship PRESETS=('default') live and the pair commented out.

    Only uncommenting the pair would leave two live assignments, and which
    one wins is decided by their order in the file rather than on purpose.
    The sample is the layout linux-g14 shipped, kept as the case that was
    actually seen even though that kernel came off the offer on 2026-08-21.
    """
    default_only = (
        "ALL_kver=\"/boot/vmlinuz-linux-g14\"\n"
        "PRESETS=('default')\n"
        "#PRESETS=('default' 'fallback')\n"
    )
    out = chroot._enable_fallback_preset(default_only)
    assert _presets_lines(out) == ["PRESETS=('default' 'fallback')"]


def test_fallback_preset_leaves_the_stock_arch_layout_alone():
    stock = "ALL_kver=\"/boot/vmlinuz-linux\"\nPRESETS=('default' 'fallback')\n"
    out = chroot._enable_fallback_preset(stock)
    assert _presets_lines(out) == ["PRESETS=('default' 'fallback')"]


def test_fallback_preset_added_when_there_is_nothing_to_revive():
    out = chroot._enable_fallback_preset("PRESETS=('default')\n")
    assert _presets_lines(out) == ["PRESETS=('default' 'fallback')"]


def test_watchdog_amd_uki_target(tmp_path, monkeypatch):
    etc = tmp_path / "etc"
    (etc / "kernel").mkdir(parents=True)
    (tmp_path / "usr").mkdir()
    cmdline = etc / "kernel" / "cmdline"
    cmdline.write_text("root=PARTUUID=x rw quiet\n")
    chroot_calls = []
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "chroot_run", lambda a: chroot_calls.append(a) or 0)
    monkeypatch.setattr(chroot.hardware, "cpu_matches", lambda q: q == "amd")

    assert chroot.disable_watchdog() == 0
    assert cmdline.read_text().strip().endswith("nowatchdog")
    assert "sp5100_tco" in (etc / "modprobe.d" / "blacklist-watchdog.conf").read_text()
    assert ["mkinitcpio", "-P"] in chroot_calls  # UKI: cmdline imaja gömülü

    chroot_calls.clear()
    chroot.disable_watchdog()
    assert cmdline.read_text().count("nowatchdog") == 1
    assert chroot_calls == []  # değişiklik yok -> regen yok


def test_watchdog_grub_target(tmp_path, monkeypatch):
    """GRUB kurulu hedefte parametre /etc/default/grub'a gitmeli."""
    etc = tmp_path / "etc"
    (etc / "default").mkdir(parents=True)
    (tmp_path / "usr").mkdir()
    (tmp_path / "boot" / "grub").mkdir(parents=True)
    (tmp_path / "boot" / "grub" / "grub.cfg").write_text("#\n")
    grub_default = etc / "default" / "grub"
    grub_default.write_text('GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"\n')
    chroot_calls = []
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "chroot_run", lambda a: chroot_calls.append(a) or 0)
    monkeypatch.setattr(chroot.hardware, "cpu_matches", lambda q: q == "intel")

    assert chroot.disable_watchdog() == 0
    assert "modprobe.blacklist=iTCO_wdt" in grub_default.read_text()
    assert ["grub-mkconfig", "-o", "/boot/grub/grub.cfg"] in chroot_calls


def test_enable_services_networkd_fallback(tmp_path, monkeypatch, runlog):
    (tmp_path / "etc").mkdir()
    (tmp_path / "usr").mkdir()
    installed = {"openssh", "iwd"}  # networkmanager YOK
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "run", runlog)
    monkeypatch.setattr(chroot, "_target_has", lambda p: p in installed)
    monkeypatch.setattr(chroot, "ask_yes", lambda prompt: True)

    assert chroot.enable_services() == 0
    assert ["systemctl", "--root", str(tmp_path), "enable", "sshd"] in runlog.calls
    assert ["systemctl", "--root", str(tmp_path), "enable", "iwd"] in runlog.calls
    # NetworkManager yok -> kablolu DHCP için networkd yapılandırıldı
    network_conf = tmp_path / "etc/systemd/network/20-wired.network"
    assert "DHCP=yes" in network_conf.read_text()
    assert ["systemctl", "--root", str(tmp_path), "enable",
            "systemd-networkd", "systemd-resolved"] in runlog.calls
    assert (tmp_path / "etc/resolv.conf").is_symlink()

    # NetworkManager varsa networkd sorusu hiç sorulmaz
    runlog.calls.clear()
    installed.add("networkmanager")
    monkeypatch.setattr(chroot, "ask_yes", lambda prompt: pytest.fail("sorulmamalı"))
    assert chroot.enable_services() == 0
    assert ["systemctl", "--root", str(tmp_path), "enable", "NetworkManager"] in runlog.calls


def test_enable_services_turns_trim_on(tmp_path, monkeypatch, runlog):
    """No install ever enabled fstrim.timer, so every SSD ran without TRIM.

    Ungated on purpose: util-linux is in base, and its unit already skips
    filesystems that cannot be trimmed.
    """
    (tmp_path / "etc").mkdir()
    (tmp_path / "usr").mkdir()
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "run", runlog)
    monkeypatch.setattr(chroot, "_target_has", lambda p: p == "networkmanager")

    assert chroot.enable_services() == 0
    assert ["systemctl", "--root", str(tmp_path), "enable", "fstrim.timer"] in runlog.calls



def test_installer_writes_the_writeback_limits_into_the_target(tmp_path, monkeypatch):
    """Kurulum da fstrim gibi: yoksa her makine cekirdek varsayilaniyla cikar.

    Uygulanmiyor ve geri okunmuyor -- hedef calisan sistem degil; iki dosya da
    ilk acilista yururluge girer.
    """
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "target_ready", lambda: True)
    monkeypatch.setattr(writeback, "backing_disk", lambda mountpoint: "nvme0n1")

    assert chroot.limit_writeback() == 0
    sysctl = tmp_path / "etc/sysctl.d" / writeback.SYSCTL_CONF.name
    rules = tmp_path / "etc/udev/rules.d" / writeback.UDEV_RULES.name
    assert "vm.dirty_ratio = 3" in sysctl.read_text()
    assert 'ENV{ID_USB_TYPE}=="disk"' in rules.read_text()


def test_installer_skips_the_rule_when_the_target_boots_from_usb(tmp_path, monkeypatch):
    """Kural kok diski de sinirlardi; o hic olculmedi, genel ayar yine yazilir."""
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "target_ready", lambda: True)
    monkeypatch.setattr(writeback, "backing_disk", lambda mountpoint: "sda")
    monkeypatch.setattr(writeback, "udev_property", lambda device, name: "disk")

    assert chroot.limit_writeback() == 0
    assert (tmp_path / "etc/sysctl.d" / writeback.SYSCTL_CONF.name).exists()
    assert not (tmp_path / "etc/udev/rules.d").exists()

def _meminfo(tmp_path, mem_kb: int):
    path = tmp_path / "meminfo"
    path.write_text(f"MemTotal:       {mem_kb} kB\nMemFree:         100 kB\n")
    return path


@pytest.fixture
def swap_env(tmp_path, monkeypatch, runlog):
    """create_swapfile() with the machine under the test held still.

    Pinned rather than read: _target_free_mib() would statvfs the suite's own
    tmpdir and _fstype() would answer for whatever filesystem the runner is
    on, so the ceiling test and the btrfs test would both pass or fail by
    accident of where the suite was checked out.
    """
    monkeypatch.setattr(hardware, "MEMINFO", _meminfo(tmp_path, 28474852))
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "target_ready", lambda: True)
    monkeypatch.setattr(chroot, "run", runlog)
    monkeypatch.setattr(chroot, "_target_free_mib", lambda: 200000)
    monkeypatch.setattr(chroot, "_fstype", lambda path: "ext4")
    return tmp_path


def test_swapfile_default_follows_ram(swap_env, monkeypatch, runlog):
    """A flat 8192 MiB while archsetup writes resume= is a promise it cannot keep.

    Measured on a 27.2 GiB laptop: swap 8.0 GiB against an image target of
    10.76 GiB, and hibernation there entered and never came back.
    """
    _feed(monkeypatch, chroot, [""])  # varsayilani kabul et

    assert chroot.create_swapfile() == 0
    alloc = next(c for c in runlog.calls if c[0] == "fallocate")
    assert f"{28474852 // 1024}M" in alloc


def test_swapfile_falls_back_when_ram_cannot_be_read(swap_env, monkeypatch, runlog):
    """An unmeasurable machine keeps the behaviour it used to get."""
    monkeypatch.setattr(hardware, "MEMINFO", swap_env / "absent")
    _feed(monkeypatch, chroot, [""])

    assert chroot.create_swapfile() == 0
    alloc = next(c for c in runlog.calls if c[0] == "fallocate")
    assert f"{chroot.SWAP_FALLBACK_MIB}M" in alloc


def test_swapfile_refuses_a_size_the_target_cannot_hold(swap_env, monkeypatch, runlog):
    """The ceiling is checked before anything is written.

    A swapfile that does not fit is written short and mkswap accepts it
    anyway, so nothing reports the problem until hibernation needs the room.
    """
    monkeypatch.setattr(chroot, "_target_free_mib", lambda: 4096)
    _feed(monkeypatch, chroot, ["8192"])

    assert chroot.create_swapfile() == 1
    assert not [c for c in runlog.calls if c[0] in ("fallocate", "dd", "mkswap")]


def test_swapfile_below_ram_is_a_question_not_a_refusal(swap_env, monkeypatch, runlog):
    """Smaller than RAM is a bet, and the person making it gets told so.

    A lean machine may still want it, so this asks; a run with no one to
    answer takes the side that does not silently promise hibernation.
    """
    _feed(monkeypatch, chroot, ["4096"])
    monkeypatch.setattr(chroot, "ask_yes", lambda prompt: False)

    assert chroot.create_swapfile() == 1
    assert not [c for c in runlog.calls if c[0] == "fallocate"]

    monkeypatch.setattr(chroot, "ask_yes", lambda prompt: True)
    _feed(monkeypatch, chroot, ["4096"])
    assert chroot.create_swapfile() == 0
    assert next(c for c in runlog.calls if c[0] == "fallocate")[2] == "4096M"


def test_swapfile_on_btrfs_is_marked_nocow_while_still_empty(swap_env, monkeypatch, runlog):
    """Measured: a COW file cannot be swap, and the flag will not go on later.

    dd and mkswap both return 0 on btrfs and swapon then fails with EINVAL,
    so every btrfs install this tool performed produced a swapfile it could
    not turn on. `chattr +C` fixes it only on a file with nothing in it --
    on one that already holds bytes it returns 0 and sets no flag -- so the
    order here is load-bearing, not cosmetic.
    """
    monkeypatch.setattr(chroot, "_fstype", lambda path: "btrfs")
    _feed(monkeypatch, chroot, [""])

    assert chroot.create_swapfile() == 0
    names = [c[0] for c in runlog.calls]
    assert "chattr" in names
    assert names.index("chattr") < names.index("fallocate")
    assert names.index("touch") < names.index("chattr")


def test_swapfile_falls_back_to_dd_where_fallocate_is_unsupported(swap_env, monkeypatch):
    """ext2/ext3/jfs are on the menu, and fallocate does not work there.

    Its exit code is the check rather than a list of filesystems to keep in
    step with disk.ROOT_FS.
    """
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return 1 if cmd[0] == "fallocate" else 0

    monkeypatch.setattr(chroot, "run", run)
    monkeypatch.setattr(chroot, "ask_yes", lambda prompt: True)
    _feed(monkeypatch, chroot, ["1024"])

    assert chroot.create_swapfile() == 0
    dd = next(c for c in calls if c[0] == "dd")
    assert "count=1024" in dd


def test_pickers_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(pickers, "MNT", tmp_path)
    # keymaps
    kb = tmp_path / "usr/share/kbd/keymaps/i386/qwerty"
    kb.mkdir(parents=True)
    (kb / "trq.map.gz").write_bytes(b"")
    (kb / "us.map.gz").write_bytes(b"")
    assert pickers.keymaps() == ["trq", "us"]
    # locales (locale.gen'den)
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/locale.gen").write_text(
        "# yorum satırı\n#tr_TR.UTF-8 UTF-8\n#en_US.UTF-8 UTF-8\n#tr_TR ISO-8859-9\n"
    )
    assert pickers.locales() == ["en_US", "tr_TR"]
    # saat dilimleri (posix/right hariç, iç içe şehirler dahil)
    zi = tmp_path / "usr/share/zoneinfo"
    (zi / "Europe").mkdir(parents=True)
    (zi / "Europe" / "Istanbul").write_bytes(b"")
    (zi / "America" / "Argentina").mkdir(parents=True)
    (zi / "America" / "Argentina" / "Ushuaia").write_bytes(b"")
    (zi / "posix").mkdir()
    assert pickers.timezone_regions() == ["America", "Europe"]
    assert pickers.timezone_cities("Europe") == ["Istanbul"]
    assert pickers.timezone_cities("America") == ["Argentina/Ushuaia"]


def test_setters_accept_picked_values(tmp_path, monkeypatch):
    (tmp_path / "etc").mkdir()
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "target_ready", lambda: True)
    monkeypatch.setattr(chroot, "ask_yes", lambda prompt: False)
    # input() çağrılmamalı — değer listeden geldi
    monkeypatch.setattr(
        chroot, "input", lambda *a: pytest.fail("prompt açılmamalı"), raising=False
    )
    assert chroot.set_vconsole("trf") == 0
    assert "KEYMAP=trf" in (tmp_path / "etc/vconsole.conf").read_text()


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
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "ESP", tmp_path / "efi")
    monkeypatch.setattr(chroot, "_target_has", lambda pkg: False)
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
    monkeypatch.setattr(bootloaders, "ask_yes", lambda q: False)
    assert bootloaders.install_systemd_boot() == 0
    cmdline = (boot_env / "etc" / "kernel" / "cmdline").read_text().strip()
    assert cmdline == (
        "root=PARTUUID=abcd-1234 quiet rw rootfstype=ext4 systemd.unit=graphical.target"
    )
    assert (boot_env / "efi/loader/loader.conf").exists()
    assert (boot_env / "etc/pacman.d/hooks/95-systemd-boot.hook").exists()


def test_systemd_boot_offers_uki_generation(boot_env, monkeypatch):
    """Without a UKI there are no loader entries either — nothing to boot."""
    monkeypatch.setattr(bootloaders.disk, "is_efi", lambda: True)
    monkeypatch.setattr(bootloaders, "ask_yes", lambda q: True)
    called = []
    monkeypatch.setattr(bootloaders, "gen_uki", lambda: called.append(True) or 0)
    assert bootloaders.install_systemd_boot() == 0
    assert called == [True]


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


# --- third-party kernel repositories ---------------------------------------


def _repo_env(tmp_path, monkeypatch, runlog, live_text="[core]\n"):
    live = tmp_path / "pacman.conf"
    live.write_text(live_text)
    (tmp_path / "etc").mkdir(exist_ok=True)
    (tmp_path / "etc" / "pacman.conf").write_text("[core]\n")
    monkeypatch.setattr(base, "LIVE_PACMAN_CONF", live)
    monkeypatch.setattr(base, "MNT", tmp_path)
    monkeypatch.setattr(base.disk, "guard", lambda: True)
    monkeypatch.setattr("os.path.ismount", lambda p: True)
    monkeypatch.setattr(base, "run", runlog)
    return live


def test_kernel_repo_is_added_before_pacstrap(tmp_path, monkeypatch, runlog):
    """pacstrap resolves against the *live* pacman.conf.

    Without the kernel's repository there the package is simply "target not
    found" and the whole base install fails. linux-ogc is the only out-of-tree
    kernel left on the offer; linux-g14 came off it on 2026-08-21.
    """
    kernel = "linux-ogc"
    live = _repo_env(tmp_path, monkeypatch, runlog)
    monkeypatch.setattr(base, "ask_yes", lambda q: True)
    _feed(monkeypatch, base, [str(base.KERNELS.index(kernel) + 1)])

    name = base.kernel_repo(kernel).name
    assert base.pacstrap_base() == 0
    commands = [" ".join(call) for call in runlog.calls]
    lsign = next(i for i, c in enumerate(commands) if "--lsign-key" in c)
    pacstrap = next(i for i, c in enumerate(commands) if c.startswith("pacstrap"))
    assert lsign < pacstrap
    assert f"[{name}]" in live.read_text()
    # The installed system needs the repo too, or its kernel has no updates.
    assert f"[{name}]" in (tmp_path / "etc" / "pacman.conf").read_text()


def test_g14_is_off_the_offer_but_still_a_name_to_outrank():
    """Dropping the kernel must not drop the guard; they are separate things.

    [g14] published nothing after 2026-07-19, so a linux-g14 chosen here would
    stop getting updates the moment it was installed -- it came off the list on
    2026-08-21 along with the repository's address and key. What could not come
    off with it is the name: the stanza is still in /etc/pacman.conf on every
    machine an older archsetup wrote it to, and [ogc] has to land above it.
    """
    assert "linux-g14" not in base.KERNELS
    assert base.kernel_repo("linux-g14") is None
    assert not hasattr(repos, "G14")
    assert repos.OUTRANKED == "g14"


def test_ogc_lands_above_a_leftover_g14_not_at_the_end(tmp_path, monkeypatch, runlog):
    """Order beats version: appended below [g14], [ogc] would never be read.

    archsetup stopped writing [g14] on 2026-08-21, which is why this case is
    still here rather than gone with it: the stanza stays on every machine an
    older archsetup wrote it to, and the file is what pacman reads.
    """
    live = _repo_env(
        tmp_path, monkeypatch, runlog, live_text="[core]\n\n[g14]\nServer = y\n"
    )
    monkeypatch.setattr(base, "ask_yes", lambda q: True)
    _feed(monkeypatch, base, [str(base.KERNELS.index("linux-ogc") + 1)])

    assert base.pacstrap_base() == 0
    text = live.read_text()
    assert text.index("[core]") < text.index("[ogc]") < text.index("[g14]")


def test_kernel_repo_refusal_aborts_instead_of_failing_in_pacstrap(
    tmp_path, monkeypatch, runlog
):
    _repo_env(tmp_path, monkeypatch, runlog)
    monkeypatch.setattr(base, "ask_yes", lambda q: False)
    _feed(monkeypatch, base, [str(base.KERNELS.index("linux-ogc") + 1)])

    assert base.pacstrap_base() == 1
    assert runlog.calls == []


def test_stock_kernel_needs_no_repository(tmp_path, monkeypatch, runlog):
    """linux-zen is in [extra]; nothing may be asked or written for it."""
    live = _repo_env(tmp_path, monkeypatch, runlog)
    monkeypatch.setattr(base, "ask_yes", lambda q: False)
    _feed(monkeypatch, base, [str(base.KERNELS.index("linux-zen") + 1)])

    assert base.pacstrap_base() == 0
    assert live.read_text() == "[core]\n"


def test_kernel_repo_line_withheld_when_the_key_fails(tmp_path, monkeypatch):
    """A repo pacman cannot verify breaks every later -Sy, pacstrap included."""
    conf = tmp_path / "pacman.conf"
    conf.write_text("[core]\n")
    monkeypatch.setattr(base, "run", lambda cmd, **kw: 1)
    assert base._add_repo(repos.OGC, conf, []) != 0
    assert "[ogc]" not in conf.read_text()


# --- Secure Boot -----------------------------------------------------------


@pytest.fixture
def sb_env(tmp_path, monkeypatch, runlog):
    esp = tmp_path / "efi"
    (esp / "EFI/systemd").mkdir(parents=True)
    (esp / "EFI/systemd/systemd-bootx64.efi").write_bytes(b"MZ")
    (esp / "EFI/BOOT").mkdir(parents=True)
    (esp / "EFI/BOOT/BOOTX64.EFI").write_bytes(b"MZ")
    (esp / "EFI/Linux").mkdir(parents=True)
    (esp / "EFI/Linux/arch.efi").write_bytes(b"MZ")
    (esp / "EFI/Linux/arch-fallback.efi").write_bytes(b"MZ")
    presets = tmp_path / "etc/mkinitcpio.d"
    presets.mkdir(parents=True)
    # Both passes, the way gen_uki() writes the file when a fallback was
    # asked for -- which is the default whenever the ESP has room.
    (presets / "linux-zen.preset").write_text(
        "PRESETS=('default' 'fallback')\n"
        'default_uki="/efi/EFI/Linux/arch.efi"\n'
        'fallback_uki="/efi/EFI/Linux/arch-fallback.efi"\n'
    )

    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "ESP", esp)
    monkeypatch.setattr(chroot, "target_ready", lambda: True)
    monkeypatch.setattr(chroot, "_target_has", lambda pkg: pkg == "sbctl")
    monkeypatch.setattr(chroot, "chroot_run", runlog)
    return tmp_path


def test_secure_boot_signs_what_the_firmware_loads(sb_env, monkeypatch, runlog):
    """`bootctl install` already put *unsigned* copies on the ESP.

    Signing only /usr/lib/systemd/... leaves those untouched, so the
    install reports "signed" and the machine still fails Secure Boot.
    """
    monkeypatch.setattr(chroot, "setup_mode", lambda: True)
    assert chroot.setup_secure_boot() == 0

    signed = {call[-1] for call in runlog.calls if call[:2] == ["sbctl", "sign"]}
    assert "/efi/EFI/systemd/systemd-bootx64.efi" in signed
    assert "/efi/EFI/BOOT/BOOTX64.EFI" in signed
    assert "/efi/EFI/Linux/arch.efi" in signed
    assert f"{chroot.SDBOOT_SRC}.signed" in {
        call[call.index("-o") + 1] for call in runlog.calls if "-o" in call
    }


def test_secure_boot_keeps_the_uki_out_of_the_sbctl_database(sb_env, monkeypatch, runlog):
    """-s records a permanent entry, and a UKI dies with its kernel.

    Nothing removes it on an mkinitcpio-preset machine, and sbctl's
    PostTransaction pacman hook runs `sign-all`, so the dangling entry
    fails every later transaction. Measured 2026-08-17 with linux-g14.
    """
    monkeypatch.setattr(chroot, "setup_mode", lambda: True)
    assert chroot.setup_secure_boot() == 0

    uki = next(c for c in runlog.calls if c[-1] == "/efi/EFI/Linux/arch.efi")
    assert uki == ["sbctl", "sign", "/efi/EFI/Linux/arch.efi"]
    # ...while the long-lived boot binaries do get recorded.
    assert ["sbctl", "sign", "-s", "/efi/EFI/BOOT/BOOTX64.EFI"] in runlog.calls


def test_secure_boot_signs_the_fallback_uki_too(sb_env, monkeypatch, runlog):
    """The recovery image is the one that must boot when the default will not.

    Measured 2026-08-30 in QEMU under Secure Boot firmware with keys really
    enrolled -- the first time this arm ran anywhere. The step signed
    `arch-linux-zen.efi`, exited 0, and closed with its own verify saying
    `arch-linux-zen-fallback.efi is not signed`. On an enrolled machine that
    entry is refused by the firmware, so the run produced exactly what the
    QEMU README calls worse than having no recovery entry at all: you think
    you have one.

    The cause was reading `default_uki` and nothing else, while the preset
    the same tool writes names two passes.
    """
    monkeypatch.setattr(chroot, "setup_mode", lambda: True)
    assert chroot.setup_secure_boot() == 0

    signed = {call[-1] for call in runlog.calls if call[:2] == ["sbctl", "sign"]}
    assert "/efi/EFI/Linux/arch.efi" in signed
    assert "/efi/EFI/Linux/arch-fallback.efi" in signed


def test_a_uki_the_preset_names_but_never_wrote_is_not_signed(sb_env, monkeypatch, runlog):
    """Paths are signed because the file is there, not because it was named."""
    preset = sb_env / "etc/mkinitcpio.d/linux-zen.preset"
    preset.write_text(
        preset.read_text() + 'rescue_uki="/efi/EFI/Linux/never-built.efi"\n'
    )
    monkeypatch.setattr(chroot, "setup_mode", lambda: True)
    assert chroot.setup_secure_boot() == 0
    assert not any(c[-1].endswith("never-built.efi") for c in runlog.calls)


def test_secure_boot_plants_no_resigning_hook(sb_env, monkeypatch, runlog):
    """sbctl already signs each rebuilt UKI from its own mkinitcpio post hook.

    Ours ran `sbctl sign-all`, which walks the database and therefore
    skipped the very file mkinitcpio had just built.
    """
    monkeypatch.setattr(chroot, "setup_mode", lambda: True)
    assert chroot.setup_secure_boot() == 0
    assert not (sb_env / "etc/initcpio/post").exists()


def test_secure_boot_refuses_outside_setup_mode(sb_env, monkeypatch, runlog):
    """enroll-keys cannot write PK/KEK; signing against them would be a lie."""
    monkeypatch.setattr(chroot, "setup_mode", lambda: False)
    assert chroot.setup_secure_boot() == 1
    assert runlog.calls == []


def test_setup_mode_reads_efivarfs_past_the_attribute_header(tmp_path, monkeypatch):
    var = tmp_path / "SetupMode"
    var.write_bytes(b"\x06\x00\x00\x00\x01")
    monkeypatch.setattr(chroot, "SETUP_MODE_VAR", var)
    assert chroot.setup_mode() is True
    var.write_bytes(b"\x06\x00\x00\x00\x00")
    assert chroot.setup_mode() is False
    monkeypatch.setattr(chroot, "SETUP_MODE_VAR", tmp_path / "absent")
    assert chroot.setup_mode() is None



# --- ESP helper binaries ---------------------------------------------------


@pytest.fixture
def helper_env(tmp_path, monkeypatch):
    """A target that has both helper packages and their source files."""
    for relative in ("usr/share/edk2-shell/x64/Shell.efi", "boot/memtest86+/memtest.efi"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b"MZ-new")
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "ESP", tmp_path / "efi")
    monkeypatch.setattr(chroot, "_target_has", lambda pkg: True)
    return tmp_path


def test_esp_copy_is_rewritten_over_a_stale_one(helper_env):
    """`if not shell.is_file()` made the first install the only writer.

    sbctl's pacman hook re-signs what its database lists, so an ESP copy
    left behind by an upgraded package stays signed while its source moves
    on -- and every check reports it as fine.
    """
    stale = helper_env / "efi/shellx64.efi"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"MZ-old")

    assert chroot.place_esp_helpers() == 0
    assert stale.read_bytes() == b"MZ-new"


def test_esp_helpers_get_a_hook_that_outlives_the_installer(helper_env):
    """The installer runs once; the staleness starts at the next upgrade."""
    assert chroot.place_esp_helpers() == 0
    hook = helper_env / "etc/pacman.d/hooks" / chroot.ESP_HELPERS_HOOK_NAME
    text = hook.read_text()
    assert "Target = edk2-shell" in text
    assert "Target = memtest86+-efi" in text
    # Signs what it just copied instead of waiting for zz-sbctl.hook, whose
    # Path triggers may or may not match the source of the upgrade.
    assert "sbctl sign -s" in text
    # An unmounted /efi is a directory on the root filesystem; writing the
    # copy there fills / and the ESP mount hides it.
    assert "mountpoint -q /efi" in text


def test_memtest_lands_on_the_esp_with_an_entry_that_sorts_last(helper_env):
    """/boot is inside the root filesystem here, so the firmware cannot see it."""
    loader = helper_env / "efi/loader"
    loader.mkdir(parents=True)
    (loader / "loader.conf").write_text("timeout  3\n")

    assert chroot.place_esp_helpers() == 0
    assert (helper_env / "efi/EFI/memtest86+/memtest.efi").read_bytes() == b"MZ-new"
    entry = (loader / "entries" / chroot.MEMTEST_ENTRY).read_text()
    assert "efi      /EFI/memtest86+/memtest.efi" in entry
    assert "sort-key zz-memtest" in entry
    # A version in the title would be right for one upgrade only.
    assert "7.20" not in entry


def test_memtest_entry_waits_for_a_menu_to_put_it_in(helper_env):
    """GRUB gets its own entry from /etc/grub.d/60_memtest86+-efi."""
    assert chroot.place_esp_helpers() == 0
    assert (helper_env / "efi/EFI/memtest86+/memtest.efi").is_file()
    assert not (helper_env / "efi/loader/entries").exists()


def test_systemd_boot_places_the_helpers_once_the_menu_exists(boot_env, monkeypatch):
    monkeypatch.setattr(bootloaders.disk, "is_efi", lambda: True)
    monkeypatch.setattr(bootloaders, "ask_yes", lambda q: False)
    source = boot_env / "boot/memtest86+/memtest.efi"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"MZ")
    monkeypatch.setattr(chroot, "_target_has", lambda pkg: pkg == "memtest86+-efi")

    assert bootloaders.install_systemd_boot() == 0
    assert (boot_env / "efi/EFI/memtest86+/memtest.efi").read_bytes() == b"MZ"
    assert (boot_env / "efi/loader/entries" / chroot.MEMTEST_ENTRY).is_file()


def test_secure_boot_signs_every_helper_on_the_esp(sb_env, monkeypatch, runlog):
    """Signing follows what is on the ESP, not which package is installed."""
    monkeypatch.setattr(chroot, "setup_mode", lambda: True)
    monkeypatch.setattr(chroot, "_target_has", lambda pkg: pkg in ("sbctl", "memtest86+-efi"))
    source = sb_env / "boot/memtest86+/memtest.efi"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"MZ")

    assert chroot.setup_secure_boot() == 0
    assert ["sbctl", "sign", "-s", "/efi/EFI/memtest86+/memtest.efi"] in runlog.calls


# --- disk inventory, shared gates, prepare and erase ------------------------


def _disk(path, *, tran="nvme", size="1T", model="SSD", discard=0, size_bytes=1024):
    return blockdev.Disk(
        path=path, size=size, tran=tran, model=model,
        discard=discard, size_bytes=size_bytes,
    )


@pytest.fixture
def erase_env(monkeypatch, runlog):
    monkeypatch.setattr(erase, "guard", lambda: True)
    monkeypatch.setattr(erase, "run", runlog)
    monkeypatch.setattr(nvme, "ensure_tool", lambda: True)
    monkeypatch.setattr(nvme, "crypto_supported", lambda dev: False)
    monkeypatch.setattr(nvme, "run", runlog)
    monkeypatch.setattr(blockdev, "list_disks", lambda: [_disk("/dev/nvme0n1")])
    return runlog


def test_list_disks_keeps_spaces_in_model_names(monkeypatch):
    """Measured 2026-08-30: -n shifts columns when TRAN is empty and -r
    escapes the spaces to \\x20, so the reader parses JSON."""
    payload = (
        '{"blockdevices":['
        '{"name":"/dev/sda","size":"58,7G","type":"disk","tran":"usb",'
        '"model":"Cruzer Force"},'
        '{"name":"/dev/zram0","size":"8G","type":"disk","tran":null,"model":null},'
        '{"name":"/dev/sda1","size":"1G","type":"part","tran":"usb","model":null}]}'
    )
    monkeypatch.setattr(
        blockdev.subprocess, "run",
        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": payload})(),
    )
    disks = blockdev.list_disks()
    assert [d.path for d in disks] == ["/dev/sda", "/dev/zram0"]
    assert disks[0].model == "Cruzer Force"
    assert disks[1].tran == ""


def test_the_disk_list_excludes_the_floppy():
    """A QEMU run put /dev/fd0 among the disks offered for erasure.

    4 KB, TYPE=disk, major 2, and no hardware here has a floppy to notice
    it on -- but QEMU is where the installer is exercised, and this is the
    list a user picks a disk to destroy from. Majors measured in the guest:
    fd0 2, loop 7, sr0 11.
    """
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return type("P", (), {"returncode": 0, "stdout": '{"blockdevices":[]}'})()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(blockdev.subprocess, "run", fake_run)
    blockdev.list_disks()
    monkey.undo()

    excluded = seen["cmd"][seen["cmd"].index("-e") + 1]
    assert set(excluded.split(",")) == {"2", "7", "11"}


def test_the_classifier_never_reads_rotational():
    """rotational is not a class signal and must not become one.

    Measured 2026-08-30: a SanDisk Cruzer Force USB *flash* stick reports
    rotational=1, exactly as the two platter drives on the other machine
    do. A branch built on it calls a memory stick a hard disk.

    The scan is over the AST rather than the raw lines because the prose
    here has to be free to explain the measurement -- a line-based version
    of this test flagged its own docstrings. Docstrings are dropped, every
    other string constant counts, and the detector is proved both ways on
    planted sources first.
    """

    def reads_rotational(source: str) -> bool:
        tree = ast.parse(source)
        docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
        }
        return any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "rotational" in node.value
            and node.value not in docstrings
            for node in ast.walk(tree)
        )

    assert reads_rotational('x = read(dev / "queue/rotational")')
    assert reads_rotational('def f():\n    """doc"""\n    return "rotational"\n')
    assert not reads_rotational('"""rotational is deliberately not read."""\nx = 1\n')
    assert not reads_rotational("# rotational is not consulted\nx = 1\n")

    root = Path(__file__).resolve().parents[1] / "src/archsetup/installer"
    offenders = [
        path.name
        for path in sorted(root.rglob("*.py"))
        if reads_rotational(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_erase_requires_the_device_path_typed_out(erase_env, monkeypatch):
    """A y/n prompt is too easy to answer by reflex for a whole-disk erase."""
    _feed(monkeypatch, erase, ["1", "1", "evet"])
    assert erase.erase_disk() == 1
    assert erase_env.calls == []


def test_erase_formats_an_nvme_after_confirmation(erase_env, monkeypatch):
    _feed(monkeypatch, erase, ["1", "1", "/dev/nvme0n1"])
    state.rootdev = "/dev/nvme0n1p2"
    assert erase.erase_disk() == 0
    assert ["nvme", "format", "--ses", "1", "--force", "/dev/nvme0n1"] in erase_env.calls
    # The partition it pointed at no longer exists.
    assert state.rootdev is None


def test_erase_refuses_a_disk_in_use(erase_env, monkeypatch, tmp_path):
    """A mounted partition means the whole disk is off limits."""
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/nvme0n1p2 /mnt ext4 rw 0 0\n")
    monkeypatch.setattr(blockdev, "IN_USE_SOURCES", ((str(mounts), None),))
    _feed(monkeypatch, erase, ["1"])
    assert erase.erase_disk() == 1
    assert erase_env.calls == []


def test_erase_refuses_the_medium_it_booted_from(erase_env, monkeypatch):
    """guard() asks whether we are in the live ISO, which is a different
    question from whether this is the stick we are running on."""
    monkeypatch.setattr(blockdev, "live_medium", lambda: "/dev/nvme0n1")
    _feed(monkeypatch, erase, ["1"])
    assert erase.erase_disk() == 1
    assert erase_env.calls == []


def test_erase_overwrites_a_non_nvme_disk_to_an_exact_length(erase_env, monkeypatch):
    """Sized to the byte because dd exits 1 on ENOSPC.

    Measured 2026-08-30 against /dev/full: an unbounded dd reports an error
    and exits 1 at the moment it has finished, so a completed wipe would be
    filed as a failure.
    """
    monkeypatch.setattr(
        blockdev, "list_disks",
        lambda: [_disk("/dev/sdb", tran="usb", model="KINGSTON", size_bytes=240057409536)],
    )
    _feed(monkeypatch, erase, ["1", "/dev/sdb"])
    assert erase.erase_disk() == 0
    assert [
        "dd", "if=/dev/zero", "of=/dev/sdb", "count=240057409536",
        "bs=8M", "iflag=count_bytes", "status=progress", "conv=fsync",
    ] in erase_env.calls


def test_erase_refuses_to_overwrite_a_disk_it_cannot_size(erase_env, monkeypatch):
    monkeypatch.setattr(
        blockdev, "list_disks", lambda: [_disk("/dev/sdb", tran="usb", size_bytes=0)]
    )
    _feed(monkeypatch, erase, ["1", "/dev/sdb"])
    assert erase.erase_disk() == 1
    assert erase_env.calls == []


def test_prepare_skips_blkdiscard_when_the_path_advertises_none(erase_env, monkeypatch):
    """Measured 2026-08-30: a Kingston SATA SSD reads discard_max_bytes=0
    through a USB enclosure although the drive supports TRIM. 0 means no
    discard on this path, and calling it anyway turns a supported no-op
    into an error the user has to read past."""
    monkeypatch.setattr(
        blockdev, "list_disks", lambda: [_disk("/dev/sdb", tran="usb", discard=0)]
    )
    monkeypatch.setattr(erase, "ask_yes", lambda q: True)
    _feed(monkeypatch, erase, ["1"])
    assert erase.prepare_disk() == 0
    assert ["wipefs", "-a", "/dev/sdb"] in erase_env.calls
    assert not any(call[0] == "blkdiscard" for call in erase_env.calls)


def test_prepare_discards_when_the_path_advertises_it(erase_env, monkeypatch):
    monkeypatch.setattr(
        blockdev, "list_disks",
        lambda: [_disk("/dev/nvme0n1", discard=2199023255040)],
    )
    monkeypatch.setattr(erase, "ask_yes", lambda q: True)
    _feed(monkeypatch, erase, ["1"])
    assert erase.prepare_disk() == 0
    assert ["blkdiscard", "/dev/nvme0n1"] in erase_env.calls


def test_prepare_wipes_partition_signatures_before_the_table(erase_env, monkeypatch, tmp_path):
    """Measured 2026-08-30 in QEMU, and this is what prepare exists to stop.

    `wipefs -a` on the whole disk removes the GPT and PMBR the *disk*
    carries and nothing else. An ext4 superblock inside a partition sits at
    a partition-relative offset the whole-disk call never looks at, so it
    survives -- and the guest showed it plainly: prepare reported "no
    partitions visible", one sgdisk at the same alignment later, blkid
    answered with UUID 7e83f00d-... , the UUID that was there before.

    The order is the assertion. Wiping the disk first drops the partition
    table, and with it the names of the partitions still holding
    signatures.
    """
    block = tmp_path / "block"
    for part in ("nvme0n1p1", "nvme0n1p2"):
        (block / "nvme0n1" / part).mkdir(parents=True)
        (block / "nvme0n1" / part / "partition").write_text("1\n")
    (block / "nvme0n1" / "queue").mkdir()  # a sibling that is not a partition
    monkeypatch.setattr(blockdev, "BLOCK", block)
    monkeypatch.setattr(erase, "ask_yes", lambda q: True)
    _feed(monkeypatch, erase, ["1"])

    assert erase.prepare_disk() == 0
    wipes = [c[2] for c in erase_env.calls if c[0] == "wipefs"]
    assert wipes == ["/dev/nvme0n1p1", "/dev/nvme0n1p2", "/dev/nvme0n1"]


def test_prepare_still_wipes_a_disk_with_no_partitions(erase_env, monkeypatch, tmp_path):
    """The partition sweep must not become a precondition for the disk one."""
    block = tmp_path / "block"
    (block / "nvme0n1" / "queue").mkdir(parents=True)
    monkeypatch.setattr(blockdev, "BLOCK", block)
    monkeypatch.setattr(erase, "ask_yes", lambda q: True)
    _feed(monkeypatch, erase, ["1"])

    assert erase.prepare_disk() == 0
    assert [c[2] for c in erase_env.calls if c[0] == "wipefs"] == ["/dev/nvme0n1"]


def test_format_devices_refuses_a_mounted_device(monkeypatch, tmp_path, runlog):
    """Until the shared gate existed, one ask_yes stood between a mounted
    device and mkfs -- the in-use test was private to the NVMe surface."""
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/sda2 / ext4 rw 0 0\n")
    monkeypatch.setattr(blockdev, "IN_USE_SOURCES", ((str(mounts), None),))
    monkeypatch.setattr(disk, "run", runlog)
    monkeypatch.setattr(
        disk, "ask_yes", lambda q: pytest.fail("bagli aygit icin soru sorulmamali")
    )
    state.rootdev = "/dev/sda2"
    assert disk.format_devices() == 1
    assert runlog.calls == []


# --- phase progress rows ---------------------------------------------------


def _mounts(monkeypatch, tmp_path, *lines: str) -> None:
    path = tmp_path / "phase-mounts"
    path.write_text("".join(f"{line}\n" for line in lines))
    monkeypatch.setattr(disk, "MOUNTS", path)


def test_mounted_wants_the_mountpoint_itself(monkeypatch, tmp_path):
    """/mnt/boot being mounted does not make /mnt a mountpoint."""
    _mounts(monkeypatch, tmp_path, "/dev/sda1 /mnt/boot vfat rw 0 0")
    assert disk.mounted() is False
    _mounts(monkeypatch, tmp_path, "/dev/sda2 /mnt ext4 rw 0 0")
    assert disk.mounted() is True


def test_the_disk_row_reports_selection_and_mount(monkeypatch, tmp_path):
    i18n.load("en")
    _mounts(monkeypatch, tmp_path)
    assert disk.selection_state() == "No device selected yet"

    state.rootdev = "/dev/sda2"
    state.bootdev = "/dev/sda1"
    assert disk.selection_state() == "2 device(s) selected (root /dev/sda2) · /mnt is not mounted"

    _mounts(monkeypatch, tmp_path, "/dev/sda2 /mnt ext4 rw 0 0")
    assert disk.selection_state().endswith("/mnt is mounted")


def test_the_disk_row_never_claims_a_format(monkeypatch, tmp_path):
    """Nothing records whether a device was formatted, so the row must not
    imply it. fs_packages is the attractive wrong answer: ext4 is not in
    FS_PACKAGES, so an ext4 install leaves it empty and a row keyed off it
    would report "not formatted" over a formatted disk. Moving it must not
    move the row."""
    _mounts(monkeypatch, tmp_path)
    state.rootdev = "/dev/sda2"
    before = disk.selection_state()
    state.fs_packages.extend(["btrfs-progs", "dosfstools"])
    assert disk.selection_state() == before


def test_the_base_row_has_three_states(monkeypatch, tmp_path):
    i18n.load("en")
    _mounts(monkeypatch, tmp_path)
    assert base.install_state() == "/mnt is not mounted -- phase 2 first"

    _mounts(monkeypatch, tmp_path, "/dev/sda2 /mnt ext4 rw 0 0")
    monkeypatch.setattr(base, "MNT", tmp_path / "target")
    assert base.install_state() == "Ready; pacstrap has not run"

    pacman_bin = tmp_path / "target/usr/bin/pacman"
    pacman_bin.parent.mkdir(parents=True)
    pacman_bin.write_text("")
    assert base.install_state() == "A base system is installed on /mnt"


# --- wireless: addressing and credentials ----------------------------------


@pytest.fixture
def services_env(tmp_path, monkeypatch, runlog):
    (tmp_path / "etc").mkdir()
    (tmp_path / "usr").mkdir()
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "run", runlog)
    monkeypatch.setattr(chroot, "ask_yes", lambda prompt: True)
    return tmp_path


def test_wireless_gets_an_address_too(services_env, monkeypatch):
    """iwd associates but does not address the link.

    EnableNetworkConfiguration defaults to off, so enabling iwd while
    writing a .network file for `en*` only left a laptop authenticated
    with no IP at all.
    """
    monkeypatch.setattr(chroot, "_target_has", lambda pkg: pkg == "iwd")
    assert chroot.enable_services() == 0

    wireless = services_env / "etc/systemd/network/20-wireless.network"
    assert "Name=wl*" in wireless.read_text()
    assert "DHCP=yes" in wireless.read_text()
    # Wired must win when both links are up.
    wired = (services_env / "etc/systemd/network/20-wired.network").read_text()
    assert "RouteMetric=100" in wired and "RouteMetric=600" in wireless.read_text()

    # networkd owns addressing, so iwd must be pinned to authentication only.
    main_conf = (services_env / "etc/iwd/main.conf").read_text()
    assert "EnableNetworkConfiguration = false" in main_conf
    assert "[General]" in main_conf


def test_no_iwd_config_written_without_iwd(services_env, monkeypatch):
    monkeypatch.setattr(chroot, "_target_has", lambda pkg: False)
    assert chroot.enable_services() == 0
    assert not (services_env / "etc/iwd/main.conf").exists()
    assert not (services_env / "etc/systemd/network/20-wireless.network").exists()


def test_copy_network_config_carries_profiles_with_tight_permissions(
    tmp_path, monkeypatch
):
    live = tmp_path / "live-iwd"
    live.mkdir()
    (live / "HomeNet.psk").write_text("[Security]\nPassphrase=hunter2\n")
    (live / "Open.open").write_text("[Settings]\n")
    target_root = tmp_path / "mnt"
    (target_root / "etc").mkdir(parents=True)
    (target_root / "usr").mkdir()

    monkeypatch.setattr(chroot, "MNT", target_root)
    monkeypatch.setattr(chroot, "IWD_STATE", live)
    monkeypatch.setattr(chroot, "_target_has", lambda pkg: True)
    monkeypatch.setattr(chroot, "ask_yes", lambda prompt: True)

    assert chroot.copy_network_config() == 0
    copied = target_root / "var/lib/iwd/HomeNet.psk"
    assert copied.read_text() == "[Security]\nPassphrase=hunter2\n"
    # The file holds the passphrase.
    assert copied.stat().st_mode & 0o777 == 0o600
    assert copied.parent.stat().st_mode & 0o777 == 0o700
    assert (target_root / "var/lib/iwd/Open.open").exists()


def test_copy_network_config_requires_iwd_in_the_target(tmp_path, monkeypatch, capsys):
    (tmp_path / "etc").mkdir()
    (tmp_path / "usr").mkdir()
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "_target_has", lambda pkg: False)
    assert chroot.copy_network_config() == 1
    assert not (tmp_path / "var").exists()


# --- ESP partition type ---


@pytest.fixture
def esp_env(monkeypatch, runlog):
    """lsblk cevaplarini ve konumu sahte, sfdisk cagrisini kayitli birak."""
    monkeypatch.setattr(disk, "run", runlog)
    monkeypatch.setattr(disk, "_partition_location", lambda dev: ("/dev/sda", "1"))
    return runlog


def _lsblk(monkeypatch, parttype, pttype="gpt"):
    monkeypatch.setattr(
        disk, "_lsblk_value",
        lambda dev, column: parttype if column == "PARTTYPE" else pttype,
    )


def test_correct_esp_type_asks_nothing(monkeypatch, esp_env):
    _lsblk(monkeypatch, disk.ESP_GUID)
    monkeypatch.setattr(disk, "ask_yes", lambda q: pytest.fail("sorulmamaliydi"))
    assert disk.ensure_esp_type("/dev/sda1") == 0
    assert esp_env.calls == []


def test_wrong_esp_type_is_offered_a_fix(monkeypatch, esp_env):
    # "Linux filesystem": bicimlendirme ve baglama sorunsuz calisir, hata
    # yalnizca bootctl calistiginda -- yani kurulumun en sonunda -- cikar.
    _lsblk(monkeypatch, "0fc63daf-8483-4772-8e79-3d69d8477de4")
    monkeypatch.setattr(disk, "ask_yes", lambda q: True)
    assert disk.ensure_esp_type("/dev/sda1") == 0
    assert ["sfdisk", "--part-type", "/dev/sda", "1", disk.ESP_GUID] in esp_env.calls


def test_wrong_esp_type_on_mbr_uses_the_type_byte(monkeypatch, esp_env):
    _lsblk(monkeypatch, "0x83", pttype="dos")
    monkeypatch.setattr(disk, "ask_yes", lambda q: True)
    assert disk.ensure_esp_type("/dev/sda1") == 0
    assert ["sfdisk", "--part-type", "/dev/sda", "1", disk.ESP_MBR] in esp_env.calls


def test_declined_fix_blocks_the_bootloader(monkeypatch, esp_env):
    _lsblk(monkeypatch, "0fc63daf-8483-4772-8e79-3d69d8477de4")
    monkeypatch.setattr(disk, "ask_yes", lambda q: False)
    assert disk.ensure_esp_type("/dev/sda1") != 0
    assert esp_env.calls == []

    # bootctl yine de calistirilmamali: /efi dolu olsa bile reddeder.
    state.bootdev = "/dev/sda1"
    monkeypatch.setattr(bootloaders.disk, "is_efi", lambda: True)
    assert bootloaders._require_efi() is False


# --- firmware boot order ---------------------------------------------------

# `efibootmgr -v` output, trimmed to the shape the parser cares about. The
# order is the one QEMU produced on 2026-08-30 after a first `bootctl
# install` from the live ISO: the two new entries land behind four PXE/HTTP
# entries and the EFI shell, which is how that guest ended up at a PXE
# prompt with nothing on screen to say why.
EFIBOOTMGR_V = """BootCurrent: 0002
Timeout: 0 seconds
BootOrder: 0000,0006,0007,000A,000B,000C
Boot0000* BootManagerMenuApp	FvVol(7cb8bdc9)/FvFile(eec25bdc)
Boot0006* UEFI PXEv4 (MAC:525400123456)	PciRoot(0x0)/Pci(0x2,0x0)/MAC(525400123456,1)
Boot0007* UEFI PXEv6 (MAC:525400123456)	PciRoot(0x0)/Pci(0x2,0x0)/MAC(525400123456,1)
Boot000A* EFI Internal Shell	FvVol(7cb8bdc9)/FvFile(7c04a583)
Boot000B* Arch Linux	HD(1,GPT,36b445f0-b7dc-4f18-be44-52c3d3a1f1ab,0x800,0xff7df)/\\EFI\\systemd\\systemd-bootx64.efi
Boot000C* Fallback Arch Linux	HD(1,GPT,36b445f0-b7dc-4f18-be44-52c3d3a1f1ab,0x800,0xff7df)/\\EFI\\systemd\\systemd-boot-fallbackx64.efi
"""

PARTUUID = "36b445f0-b7dc-4f18-be44-52c3d3a1f1ab"


@pytest.fixture
def order_env(boot_env, monkeypatch, runlog):
    """A firmware that answers, with the primary entry stuck at the back."""
    monkeypatch.setattr(bootloaders, "EFIBOOTMGR", "efibootmgr")
    monkeypatch.setattr(
        bootloaders.subprocess, "run",
        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": EFIBOOTMGR_V})(),
    )
    monkeypatch.setattr(bootloaders, "_blkid", lambda dev, tag: PARTUUID)
    return runlog


def test_boot_entry_is_promoted_to_the_front(order_env):
    """`bootctl install` appends a *new* entry; nothing else moves it.

    Read in systemd v261 (bootctl-install.c, insert_into_order): the slot
    goes to order[n] when the operation is INSTALL_NEW. Measured twice in
    QEMU on 2026-08-30 with the same command on the same ESP -- first run
    appended, second run (entry already in the order) moved it to the
    front. So the first install on any machine that already has firmware
    entries leaves Arch behind them.
    """
    assert bootloaders.promote_boot_entry("/dev/vda1") == 0
    assert ["efibootmgr", "-o", "000B,0000,0006,0007,000A,000C"] in order_env.calls


def test_the_fallback_entry_is_not_the_one_promoted(order_env):
    """Both entries carry the ESP's PARTUUID; only the loader path splits
    them, and promoting the fallback would boot the recovery image."""
    bootloaders.promote_boot_entry("/dev/vda1")
    written = [c for c in order_env.calls if c[:2] == ["efibootmgr", "-o"]]
    assert written and written[0][2].split(",")[0] == "000B"


def test_an_entry_already_first_is_left_alone(boot_env, monkeypatch, runlog):
    monkeypatch.setattr(bootloaders, "EFIBOOTMGR", "efibootmgr")
    monkeypatch.setattr(
        bootloaders.subprocess, "run",
        lambda *a, **k: type("P", (), {
            "returncode": 0,
            "stdout": EFIBOOTMGR_V.replace(
                "BootOrder: 0000,0006", "BootOrder: 000B,0000,0006"
            ).replace(",000B,000C", ",000C"),
        })(),
    )
    monkeypatch.setattr(bootloaders, "_blkid", lambda dev, tag: PARTUUID)
    assert bootloaders.promote_boot_entry("/dev/vda1") == 0
    assert not any(c[:2] == ["efibootmgr", "-o"] for c in runlog.calls)


def test_an_unreadable_order_does_not_fail_the_install(boot_env, monkeypatch, runlog):
    """The bootloader is installed either way; losing the ordering is worth
    a sentence, not a non-zero exit from the last step of an install."""
    monkeypatch.setattr(bootloaders, "EFIBOOTMGR", "/nonexistent/efibootmgr")
    monkeypatch.setattr(bootloaders, "_blkid", lambda dev, tag: PARTUUID)
    assert bootloaders.promote_boot_entry("/dev/vda1") == 0
    assert not any(c[:2] == ["efibootmgr", "-o"] for c in runlog.calls)


def test_install_promotes_the_entry_it_just_created(order_env, monkeypatch):
    monkeypatch.setattr(bootloaders.disk, "is_efi", lambda: True)
    monkeypatch.setattr(bootloaders, "ask_yes", lambda q: False)
    monkeypatch.setattr(disk, "ensure_esp_type", lambda dev: 0)
    state.bootdev = "/dev/vda1"
    assert bootloaders.install_systemd_boot() == 0
    assert ["efibootmgr", "-o", "000B,0000,0006,0007,000A,000C"] in order_env.calls


# --- GRUB on BIOS ----------------------------------------------------------


def _lsblk_pttype(monkeypatch, stdout):
    monkeypatch.setattr(
        bootloaders.subprocess, "run",
        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": stdout})(),
    )


@pytest.fixture
def bios_env(boot_env, monkeypatch, runlog):
    monkeypatch.setattr(bootloaders.disk, "is_efi", lambda: False)
    monkeypatch.setattr(bootloaders.disk, "list_devices", lambda kind: [("/dev/vda", "12G", "disk")])
    monkeypatch.setattr(bootloaders.disk, "_choose", lambda title, rows: "/dev/vda")
    # boot_env stubs chroot_run to a bare 0; the BIOS arm's whole question is
    # which chroot commands ran, so it is recorded here instead.
    monkeypatch.setattr(bootloaders, "chroot_run", runlog)
    (boot_env / "etc/default").mkdir(parents=True, exist_ok=True)
    (boot_env / "etc/default/grub").write_text(
        'GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"\n'
    )
    return runlog


def test_grub_refuses_a_gpt_disk_with_no_bios_boot_partition(bios_env, monkeypatch):
    """The layout this repo's own README describes is the UEFI one.

    Measured in QEMU: following it in BIOS mode produces a GPT disk with
    no ef02 partition, and `grub-install --target=i386-pc` ends in "will
    not proceed with blocklists". The fix -- a 1 MiB ef02 partition -- is
    nowhere in that message, so the question is asked before the install
    rather than after it fails.
    """
    _lsblk_pttype(monkeypatch, "gpt\n     0fc63daf-8483-4772-8e79-3d69d8477de4\n")
    assert bootloaders.install_grub() == 1
    assert not any(c[0] == "grub-install" for c in bios_env.calls)
    assert not any("grub-mkconfig" in c for c in bios_env.calls)


def test_grub_proceeds_when_the_bios_boot_partition_is_there(bios_env, monkeypatch):
    _lsblk_pttype(monkeypatch, "gpt\n     21686148-6449-6e6f-744e-656564454649\n")
    assert bootloaders.install_grub() == 0
    assert ["grub-install", "--target=i386-pc", "/dev/vda"] in bios_env.calls


def test_an_mbr_disk_is_never_asked_the_gpt_question(bios_env, monkeypatch):
    """An MBR label has the gap after the boot record, so the question
    does not arise -- and asking it anyway would refuse a working setup."""
    _lsblk_pttype(monkeypatch, "dos\n     0x83\n")
    assert bootloaders.install_grub() == 0
    assert ["grub-install", "--target=i386-pc", "/dev/vda"] in bios_env.calls


def test_a_failed_grub_install_does_not_reach_grub_mkconfig(bios_env, monkeypatch):
    """It used to, and grub-mkconfig succeeds on its own.

    Measured in the BIOS run: grub-install refused, the flow carried on,
    and the step ended on the word "done" with the real error scrolled
    off the top -- on a disk with no boot code in it.
    """
    _lsblk_pttype(monkeypatch, "dos\n     0x83\n")
    def failing(cmd):
        bios_env(cmd)
        return 1 if cmd[0] == "grub-install" else 0

    monkeypatch.setattr(bootloaders, "chroot_run", failing)
    assert bootloaders.install_grub() != 0
    assert not any("grub-mkconfig" in c for c in bios_env.calls)


# --- loader.conf timeout ---------------------------------------------------


def test_loader_timeout_is_numeric_by_default(boot_env, monkeypatch):
    """`timeout menu-force` is not a long timeout, it is no timeout at all.

    systemd-boot then waits for a keypress forever, so a server, a VM or a
    machine rebooted over SSH never comes back -- and from outside that looks
    exactly like a failed boot. It happened in the QEMU run.
    """
    monkeypatch.setattr(bootloaders.disk, "is_efi", lambda: True)
    monkeypatch.setattr(bootloaders, "ask_yes", lambda q: False)

    assert bootloaders.install_systemd_boot() == 0
    conf = (boot_env / "efi/loader/loader.conf").read_text()
    assert "timeout  3" in conf
    assert "menu-force" not in conf


def test_loader_menu_force_still_available(boot_env, monkeypatch):
    # Only the menu question gets a yes; a blanket yes would also accept the
    # "generate the UKI now" prompt, which needs a pacstrapped target.
    menu_q = i18n.t("inst.menu_force_q")
    monkeypatch.setattr(bootloaders.disk, "is_efi", lambda: True)
    monkeypatch.setattr(bootloaders, "ask_yes", lambda q: q == menu_q)

    assert bootloaders.install_systemd_boot() == 0
    assert "timeout  menu-force" in (boot_env / "efi/loader/loader.conf").read_text()


# --- fallback UKI vs ESP size ----------------------------------------------


@pytest.fixture
def uki_env(tmp_path, monkeypatch):
    etc = tmp_path / "etc"
    (etc / "mkinitcpio.d").mkdir(parents=True)
    (etc / "kernel").mkdir()
    (etc / "kernel" / "cmdline").write_text("root=PARTUUID=x rw\n")
    (etc / "mkinitcpio.conf").write_text("HOOKS=(base udev block filesystems fsck)\n")
    (tmp_path / "usr").mkdir()
    preset = etc / "mkinitcpio.d" / "linux.preset"
    preset.write_text(
        'ALL_kver="/boot/vmlinuz-linux"\n'
        "PRESETS=('default' 'fallback')\n"
        'default_image="/boot/initramfs-linux.img"\n'
        '#default_uki="/efi/EFI/Linux/arch-linux.efi"\n'
        'fallback_image="/boot/initramfs-linux-fallback.img"\n'
        '#fallback_uki="/efi/EFI/Linux/arch-linux-fallback.efi"\n'
        '#fallback_options="-S autodetect"\n'
    )
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "chroot_run", lambda a: 0)
    return preset


def test_fallback_skipped_when_the_esp_is_too_small(uki_env, monkeypatch):
    """A fallback UKI measured 214 MB against the default's 33 MB, because
    -S autodetect bundles every module and its firmware. Filling the ESP
    would strand the *next* kernel update, which fails far from here."""
    monkeypatch.setattr(chroot, "_esp_free_mb", lambda: 200)
    monkeypatch.setattr(chroot, "ask_yes", lambda q: False)

    assert chroot.gen_uki() == 0
    text = uki_env.read_text()
    assert "PRESETS=('default')" in text
    assert "\nfallback_uki" not in text  # still commented out
    assert 'default_uki="/efi/EFI/Linux/arch-linux.efi"' in text


def test_fallback_kept_when_asked_for_despite_tight_space(uki_env, monkeypatch):
    monkeypatch.setattr(chroot, "_esp_free_mb", lambda: 200)
    monkeypatch.setattr(chroot, "ask_yes", lambda q: True)

    assert chroot.gen_uki() == 0
    assert "PRESETS=('default' 'fallback')" in uki_env.read_text()


def test_roomy_esp_is_not_asked_about(uki_env, monkeypatch):
    monkeypatch.setattr(chroot, "_esp_free_mb", lambda: 4096)
    monkeypatch.setattr(
        chroot, "ask_yes", lambda q: pytest.fail("yer varken sorulmamali")
    )

    assert chroot.gen_uki() == 0
    assert "PRESETS=('default' 'fallback')" in uki_env.read_text()


def test_unmeasurable_esp_keeps_the_fallback(uki_env, monkeypatch):
    """Failing to stat the ESP is not a reason to drop the rescue image."""
    monkeypatch.setattr(chroot, "_esp_free_mb", lambda: None)
    monkeypatch.setattr(
        chroot, "ask_yes", lambda q: pytest.fail("olculemiyorsa sorulmamali")
    )

    assert chroot.gen_uki() == 0
    assert "PRESETS=('default' 'fallback')" in uki_env.read_text()


# --- locale input ----------------------------------------------------------


@pytest.mark.parametrize("typed", ["tr_TR", "tr_TR.UTF-8", "tr_TR.utf8", "tr_TR.UTF8"])
def test_locale_accepts_the_full_name_too(tmp_path, monkeypatch, typed):
    """The prompt says .UTF-8 is appended, but typing it is the natural
    thing to do -- and it used to build tr_TR.UTF-8.UTF-8 and hard-fail."""
    etc = tmp_path / "etc"
    etc.mkdir()
    (tmp_path / "usr").mkdir()
    (etc / "locale.gen").write_text("#en_US.UTF-8 UTF-8\n#tr_TR.UTF-8 UTF-8\n")
    monkeypatch.setattr(chroot, "MNT", tmp_path)
    monkeypatch.setattr(chroot, "chroot_run", lambda a: 0)

    assert chroot.set_locale(typed) == 0
    assert "\ntr_TR.UTF-8 UTF-8" in (etc / "locale.gen").read_text()
    assert (etc / "locale.conf").read_text().startswith("LANG=tr_TR.UTF-8\n")


# --- ParallelDownloads reaches the installed system ------------------------


def _parallel_env(tmp_path, monkeypatch, live_text="[options]\n#ParallelDownloads = 5\n"):
    """Live and target pacman.conf under tmp_path; neither is the real one."""
    live = tmp_path / "live-pacman.conf"
    live.write_text(live_text)
    monkeypatch.setattr(base, "LIVE_PACMAN_CONF", live)
    monkeypatch.setattr(base, "MNT", tmp_path / "mnt")
    return live


def _parallel_line(path):
    return [l.strip() for l in path.read_text().splitlines()
            if l.strip().startswith("ParallelDownloads")]


def test_parallel_downloads_remembers_the_choice_for_the_target(tmp_path, monkeypatch):
    """The step runs five before mount, so it cannot write the target itself.

    Measured: _set_parallel() on a file that does not exist returns False and
    writes nothing, and parallel_downloads() prints only what it wrote -- so
    the target half was skipped with no output at all. Remembering the number
    is what lets pacstrap_base() finish the job.
    """
    live = _parallel_env(tmp_path, monkeypatch)
    _feed(monkeypatch, base, ["8"])

    assert base.parallel_downloads() == 0
    assert _parallel_line(live) == ["ParallelDownloads = 8"]
    assert not (tmp_path / "mnt").exists()
    assert state.parallel_downloads == 8


def test_pacstrap_carries_parallel_downloads_into_the_target(tmp_path, monkeypatch, runlog):
    """pacstrap copies the host mirrorlist but not the host pacman.conf.

    copymirrorlist=1 is the default and copyconf=0 is, with -P not passed, so
    nothing else moves this number across. Without it the target keeps what
    the pacman package ships -- ParallelDownloads = 5, uncommented -- which is
    why the omission looked like nothing was wrong.
    """
    _repo_env(tmp_path, monkeypatch, runlog)
    target = tmp_path / "etc" / "pacman.conf"
    target.write_text("[options]\n#ParallelDownloads = 5\n")
    monkeypatch.setattr(base, "ask_yes", lambda q: False)
    _feed(monkeypatch, base, [str(base.KERNELS.index("linux-zen") + 1)])
    state.parallel_downloads = 8

    assert base.pacstrap_base() == 0
    assert _parallel_line(target) == ["ParallelDownloads = 8"]


def test_target_is_left_alone_when_no_number_was_chosen(tmp_path, monkeypatch, runlog):
    """Skipping the step is a choice; it must not write a default of its own."""
    _repo_env(tmp_path, monkeypatch, runlog)
    target = tmp_path / "etc" / "pacman.conf"
    target.write_text("[options]\n#ParallelDownloads = 5\n")
    monkeypatch.setattr(base, "ask_yes", lambda q: False)
    _feed(monkeypatch, base, [str(base.KERNELS.index("linux-zen") + 1)])

    assert state.parallel_downloads is None
    assert base.pacstrap_base() == 0
    assert _parallel_line(target) == []


def test_the_number_lands_before_the_repo_step_reads_the_file(tmp_path, monkeypatch, runlog):
    """add_kernel_repo() runs `pacman -Sy` in the target, which reads it."""
    _repo_env(tmp_path, monkeypatch, runlog)
    (tmp_path / "etc" / "pacman.conf").write_text("[core]\n#ParallelDownloads = 5\n")
    monkeypatch.setattr(base, "ask_yes", lambda q: True)
    _feed(monkeypatch, base, [str(base.KERNELS.index("linux-ogc") + 1)])
    state.parallel_downloads = 8

    assert base.pacstrap_base() == 0
    text = (tmp_path / "etc" / "pacman.conf").read_text()
    assert "ParallelDownloads = 8" in text
    assert "[ogc]" in text
