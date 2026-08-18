"""Installer (live ISO) mode: disk, pacstrap prep, chroot config, bootloaders."""

import re

import pytest

from archsetup.core import hardware, i18n, repos
from archsetup.installer import base, bootloaders, chroot, disk, nvme, pickers
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


def test_fallback_preset_survives_the_g14_layout():
    """linux-g14 ships PRESETS=('default') live and the pair commented out.

    Only uncommenting the pair would leave two live assignments, and which
    one wins is decided by their order in the file rather than on purpose.
    """
    g14 = (
        "ALL_kver=\"/boot/vmlinuz-linux-g14\"\n"
        "PRESETS=('default')\n"
        "#PRESETS=('default' 'fallback')\n"
    )
    out = chroot._enable_fallback_preset(g14)
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


@pytest.mark.parametrize("kernel", ["linux-g14", "linux-ogc"])
def test_kernel_repo_is_added_before_pacstrap(tmp_path, monkeypatch, runlog, kernel):
    """pacstrap resolves against the *live* pacman.conf.

    Without the kernel's repository there the package is simply "target not
    found" and the whole base install fails. Both out-of-tree kernels take
    this path, and they come from two different repositories.
    """
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


def test_ogc_lands_above_g14_not_at_the_end(tmp_path, monkeypatch, runlog):
    """Order beats version: appended below [g14], [ogc] would never be read."""
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
    _feed(monkeypatch, base, [str(base.KERNELS.index("linux-g14") + 1)])

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
    presets = tmp_path / "etc/mkinitcpio.d"
    presets.mkdir(parents=True)
    (presets / "linux-zen.preset").write_text('default_uki="/efi/EFI/Linux/arch.efi"\n')

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


# --- NVMe reset ------------------------------------------------------------


@pytest.fixture
def nvme_env(monkeypatch, runlog):
    monkeypatch.setattr(nvme, "guard", lambda: True)
    monkeypatch.setattr(nvme, "ensure_tool", lambda: True)
    monkeypatch.setattr(nvme, "list_namespaces", lambda: [("/dev/nvme0n1", "SSD", "1T")])
    monkeypatch.setattr(nvme, "crypto_supported", lambda dev: False)
    monkeypatch.setattr(nvme, "run", runlog)
    # busy() stays real; tests point it at a fixture file instead of /proc.
    monkeypatch.setattr(nvme, "IN_USE_SOURCES", ())
    return runlog


def test_nvme_reset_requires_the_device_path_typed_out(nvme_env, monkeypatch):
    """A y/n prompt is too easy to answer by reflex for a whole-disk erase."""
    _feed(monkeypatch, nvme, ["1", "1", "evet"])
    assert nvme.reset_namespace() == 1
    assert nvme_env.calls == []


def test_nvme_reset_formats_after_confirmation(nvme_env, monkeypatch):
    _feed(monkeypatch, nvme, ["1", "1", "/dev/nvme0n1"])
    state.rootdev = "/dev/nvme0n1p2"
    assert nvme.reset_namespace() == 0
    assert ["nvme", "format", "--ses", "1", "--force", "/dev/nvme0n1"] in nvme_env.calls
    # The partition it pointed at no longer exists.
    assert state.rootdev is None


def test_nvme_reset_refuses_a_namespace_in_use(nvme_env, monkeypatch, tmp_path):
    """A mounted partition means the whole namespace is off limits."""
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/nvme0n1p2 /mnt ext4 rw 0 0\n")
    monkeypatch.setattr(nvme, "IN_USE_SOURCES", ((str(mounts), None),))
    _feed(monkeypatch, nvme, ["1"])
    assert nvme.reset_namespace() == 1
    assert nvme_env.calls == []


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
