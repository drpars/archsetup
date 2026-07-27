"""Post-install tasks: dotfiles, sddm, kmscon, network, asus, virt, waydroid."""

import json
import shutil

import pytest

from archsetup.core import (
    asus,
    audio_dsp,
    coredump,
    dotfiles,
    hardware,
    gpuconfig,
    kmscon,
    network,
    nvidia_laptop,
    sddm,
    virt,
    waydroid,
)


@pytest.fixture
def dot_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / "config" / "kitty").mkdir(parents=True)
    (repo / "config" / "kitty" / "kitty.conf").write_text("font_size 12\n")
    (repo / "home").mkdir()
    (repo / "home" / ".zshrc").write_text("alias ll='ls -la'\n")
    backup = tmp_path / "backup"
    backup.mkdir()

    monkeypatch.setattr(dotfiles, "DOTFILES_DIR", repo)
    monkeypatch.setattr(
        dotfiles, "section_target",
        lambda s: {"config": home / ".config", "home": home}[s],
    )
    monkeypatch.setattr(dotfiles, "_backup_dir", lambda: backup)
    return tmp_path


def test_list_items(dot_env):
    assert dotfiles.list_items("config") == ["kitty"]
    assert dotfiles.list_items("home") == [".zshrc"]
    assert dotfiles.list_items("nonexistent" if False else "config")


def test_symlink_backs_up_and_validates(dot_env):
    target = dot_env / "home" / ".config" / "kitty"
    target.mkdir()
    (target / "old.conf").write_text("old\n")

    assert dotfiles.symlink_items("config", ["kitty"]) == 0
    assert target.is_symlink()
    assert (target / "kitty.conf").read_text() == "font_size 12\n"
    assert (dot_env / "backup" / "kitty" / "old.conf").exists()


def test_validate_detects_broken_link(dot_env):
    dotfiles.symlink_items("config", ["kitty"])
    shutil.rmtree(dot_env / "repo" / "config" / "kitty")
    assert dotfiles.validate_items("config", ["kitty"]) == 1


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync required")
def test_copy_via_rsync(dot_env, monkeypatch):
    monkeypatch.setattr(dotfiles, "ask_yes", lambda prompt: True)
    assert dotfiles.copy_items("home", [".zshrc"]) == 0
    copied = dot_env / "home" / ".zshrc"
    assert copied.read_text() == "alias ll='ls -la'\n" and not copied.is_symlink()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync required")
def test_wallpapers_subdir_and_git_excluded(dot_env, monkeypatch):
    wall = dot_env / "wall"
    (wall / ".git").mkdir(parents=True)
    (wall / "sunset.jpg").write_text("img")
    pics = dot_env / "Pictures"
    monkeypatch.setattr(dotfiles, "WALLPAPER_REPO_DIR", wall)
    monkeypatch.setattr(dotfiles, "ensure_repo", lambda name, target: 0)
    monkeypatch.setattr(dotfiles, "_xdg_dir", lambda name, fb: pics)
    monkeypatch.setattr(dotfiles, "ask_yes", lambda prompt: True)

    assert dotfiles.install_wallpapers() == 0
    assert (pics / "Wallpaper" / "sunset.jpg").exists()
    assert not (pics / "Wallpaper" / ".git").exists()


def test_sddm_silent(tmp_path, monkeypatch, fake_write, runlog):
    monkeypatch.setattr(sddm, "sudo_write", fake_write)
    monkeypatch.setattr(sddm, "run", runlog)
    monkeypatch.setattr(sddm, "_sddm_installed", lambda: True)
    monkeypatch.setattr(sddm.pacman, "install", lambda repo, aur: 0)
    monkeypatch.setattr(sddm, "SDDM_CONF", tmp_path / "sddm.conf")

    assert sddm.install_silent() == 0
    assert "Current=silent" in (tmp_path / "sddm.conf").read_text()

    monkeypatch.setattr(sddm, "_sddm_installed", lambda: False)
    assert sddm.install_silent() == 1


def test_sddm_sugarcandy(tmp_path, monkeypatch, fake_write, runlog):
    repo = tmp_path / "dotrepo"
    (repo / "sddm" / "sugar-candy").mkdir(parents=True)
    (repo / "sddm" / "sddm.conf").write_text("[Autologin]\nUser=drpars\n")
    (repo / "sddm" / "sugar-candy" / "sugar-candy.tar.gz").write_bytes(b"x")

    monkeypatch.setattr(sddm, "sudo_write", fake_write)
    monkeypatch.setattr(sddm, "run", runlog)
    monkeypatch.setattr(sddm, "_sddm_installed", lambda: True)
    monkeypatch.setattr(sddm, "DOTFILES_DIR", repo)
    monkeypatch.setattr(sddm, "SDDM_CONF_DIR", tmp_path / "sddm.conf.d")
    monkeypatch.setattr(sddm, "THEMES_DIR", tmp_path / "themes")

    assert sddm.install_sugarcandy() == 0
    assert "sugar-candy" in (tmp_path / "sddm.conf.d" / "10-theme.conf").read_text()
    assert any(cmd[:2] == ["sudo", "tar"] for cmd in runlog.calls)


def test_kmscon(tmp_path, monkeypatch, fake_write, runlog):
    services_log = []
    monkeypatch.setattr(kmscon, "sudo_write", fake_write)
    monkeypatch.setattr(kmscon, "run", runlog)
    monkeypatch.setattr(kmscon, "_ask_tty", lambda: 4)
    monkeypatch.setattr(kmscon.pacman, "install", lambda repo, aur: 0)
    monkeypatch.setattr(kmscon.services, "disable", lambda n: services_log.append(("d", n)) or 0)
    monkeypatch.setattr(kmscon.services, "enable", lambda n: services_log.append(("e", n)) or 0)
    monkeypatch.setattr(kmscon, "CONFIG", tmp_path / "kmscon" / "kmscon.conf")

    assert kmscon.install() == 0
    assert ("d", "getty@tty4.service") in services_log
    assert ("e", "kmsconvt@tty4.service") in services_log


def test_network_group_before_chown(tmp_path, monkeypatch, fake_write, runlog):
    monkeypatch.setattr(network, "run", runlog)
    monkeypatch.setattr(network, "sudo_write", fake_write)
    monkeypatch.setattr(network.pacman, "install", lambda repo, aur: 0)
    monkeypatch.setattr(network.pacman, "is_installed", lambda p: True)
    monkeypatch.setattr(network.services, "enable", lambda n: 0)
    monkeypatch.setattr(network, "_group_exists", lambda n: False)
    monkeypatch.setattr(network.getpass, "getuser", lambda: "drpars")
    monkeypatch.setattr(network.shutil, "which", lambda n: None)
    monkeypatch.setattr(network, "SMB_CONF", tmp_path / "smb.conf")

    assert network.configure() == 0
    conf = (tmp_path / "smb.conf").read_text()
    assert "log file = /var/log/samba/%m.log" in conf
    calls = runlog.calls
    assert calls.index(["sudo", "groupadd", "-r", "sambashare"]) < calls.index(
        ["sudo", "chown", "root:sambashare", network.USERSHARES]
    )


def test_asus_g14_routing(tmp_path, monkeypatch):
    installs, enables = [], []
    monkeypatch.setattr(asus.pacman, "install", lambda r, a: installs.append((tuple(r), tuple(a))) or 0)
    monkeypatch.setattr(asus.pacman, "is_installed", lambda p: p == "power-profiles-daemon")
    monkeypatch.setattr(asus.services, "enable", lambda n: enables.append(n) or 0)
    monkeypatch.setattr(asus.prompt, "ask_yes", lambda q: False)

    conf = tmp_path / "pacman.conf"
    conf.write_text("[options]\n[g14]\nServer = x\n")
    monkeypatch.setattr(asus, "PACMAN_CONF", conf)
    asus.install()
    assert "asusctl" in installs[0][0]  # repo'dan

    conf.write_text("[options]\n")
    asus.install()
    assert "asusctl" in installs[1][1]  # depo reddedildi -> AUR'dan
    assert enables == ["power-profiles-daemon"] * 2  # yalnız kurulu paketin servisi
    # supergfxctl artık varsayılan kümede değil (upstream aşamalı olarak kaldırıyor)
    assert all("supergfxctl" not in repo + aur for repo, aur in installs)


def test_asus_g14_setup_appends_stanza(tmp_path, monkeypatch, fake_write, runlog):
    conf = tmp_path / "pacman.conf"
    conf.write_text("[options]\n[core]\nInclude = /etc/pacman.d/mirrorlist\n")
    monkeypatch.setattr(asus, "PACMAN_CONF", conf)
    monkeypatch.setattr(asus, "run", runlog)
    monkeypatch.setattr(asus, "sudo_write", fake_write)

    assert asus.setup_g14_repo() is True
    text = conf.read_text()
    assert "[g14]" in text
    # [core] önce gelmeli: resmi depolar [g14] karşısında önceliğini korur
    assert text.index("[core]") < text.index("[g14]")
    assert ["sudo", "pacman-key", "--lsign-key", asus.G14_KEY] in runlog.calls


def test_asus_g14_setup_aborts_when_key_import_fails(tmp_path, monkeypatch):
    conf = tmp_path / "pacman.conf"
    conf.write_text("[options]\n")
    monkeypatch.setattr(asus, "PACMAN_CONF", conf)
    monkeypatch.setattr(asus, "run", lambda cmd, **kw: 1)

    assert asus.setup_g14_repo() is False
    assert "[g14]" not in conf.read_text()


def test_nvidia_laptop_configure(tmp_path, monkeypatch, fake_write, runlog):
    enabled = []
    modprobe = tmp_path / "nvidia.conf"
    rules = tmp_path / "80-nvidia-pm.rules"
    monkeypatch.setattr(nvidia_laptop, "MODPROBE_CONF", modprobe)
    monkeypatch.setattr(nvidia_laptop, "UDEV_RULES", rules)
    monkeypatch.setattr(nvidia_laptop, "run", runlog)
    monkeypatch.setattr(nvidia_laptop, "sudo_write", fake_write)
    monkeypatch.setattr(nvidia_laptop.hardware, "gpu_matches", lambda q: True)
    monkeypatch.setattr(nvidia_laptop.pacman, "is_installed", lambda p: True)
    monkeypatch.setattr(nvidia_laptop.services, "unit_exists", lambda n: True)
    monkeypatch.setattr(nvidia_laptop.services, "enable", lambda n: enabled.append(n) or 0)
    monkeypatch.setattr(nvidia_laptop.services, "enable_now", lambda n: enabled.append(n) or 0)
    monkeypatch.setattr(
        nvidia_laptop.hardware,
        "nvidia_gpu_lines",
        lambda: ["01:00.0 VGA compatible controller: NVIDIA Corporation GA106M [RTX 3060]"],
    )

    assert nvidia_laptop.configure() == 0
    conf = modprobe.read_text()
    assert "NVreg_EnableS0ixPowerManagement=1" in conf
    assert "NVreg_DynamicPowerManagement=0x02" in conf
    # Ampere: hibrit dizüstüde fbdev dGPU'yu uyandırdığı için kapalı olmalı
    assert "fbdev=0" in conf
    assert "NVreg_EnableGpuFirmware" not in conf
    assert 'ATTR{power/control}="auto"' in rules.read_text()
    assert enabled == [*nvidia_laptop.SERVICES, nvidia_laptop.POWERD_SERVICE]
    assert ["sudo", "mkinitcpio", "-P"] in runlog.calls
    assert ["sudo", "udevadm", "control", "--reload-rules"] in runlog.calls


def test_nvidia_laptop_turing_adds_firmware_option(monkeypatch):
    monkeypatch.setattr(
        nvidia_laptop.hardware,
        "nvidia_gpu_lines",
        lambda: ["01:00.0 VGA compatible controller: NVIDIA Corporation TU106M [RTX 2060]"],
    )
    assert "NVreg_EnableGpuFirmware=0" in nvidia_laptop.modprobe_content()


def test_nvidia_laptop_backs_up_differing_file(tmp_path, monkeypatch, fake_write, runlog):
    modprobe = tmp_path / "nvidia.conf"
    modprobe.write_text("options nvidia_drm modeset=1 fbdev=1\n")
    monkeypatch.setattr(nvidia_laptop, "run", runlog)
    monkeypatch.setattr(nvidia_laptop, "sudo_write", fake_write)

    rc, changed = nvidia_laptop._write(modprobe, "new content\n")
    assert (rc, changed) == (0, True)
    assert ["sudo", "cp", str(modprobe), f"{modprobe}.bak"] in runlog.calls

    # İkinci çağrı: içerik aynı, yazma da yedekleme de yok
    runlog.calls.clear()
    assert nvidia_laptop._write(modprobe, "new content\n") == (0, False)
    assert runlog.calls == []


def test_nvidia_laptop_requires_driver(monkeypatch):
    monkeypatch.setattr(nvidia_laptop.hardware, "gpu_matches", lambda q: True)
    monkeypatch.setattr(nvidia_laptop.pacman, "is_installed", lambda p: False)
    assert nvidia_laptop.configure() == 1


@pytest.fixture
def audio_home(tmp_path, monkeypatch):
    """Redirect every install target of audio_dsp into tmp_path."""
    home = tmp_path / "home"
    monkeypatch.setattr(audio_dsp, "PRESET_DIR", home / "presets")
    monkeypatch.setattr(audio_dsp, "BIN_DIR", home / "bin")
    monkeypatch.setattr(audio_dsp, "UNIT_DIR", home / "units")
    monkeypatch.setattr(audio_dsp, "AUTOSTART_DIR", home / "autostart")
    return home


def test_audio_dsp_installs_assets_and_user_service(audio_home, monkeypatch, runlog):
    monkeypatch.setattr(audio_dsp.hardware, "board_matches", lambda q: True)
    monkeypatch.setattr(audio_dsp.pacman, "install", lambda r, a: 0)
    monkeypatch.setattr(audio_dsp.pacman, "is_installed", lambda p: True)
    monkeypatch.setattr(audio_dsp.services, "run", runlog)

    assert audio_dsp.configure() == 0

    preset = audio_home / "presets" / "ROG-G513RM.json"
    assert json.loads(preset.read_text())["output"]["plugins_order"][0] == "bass_enhancer#0"
    assert json.loads((audio_home / "presets" / "Flat.json").read_text()) == {
        "output": {"blocklist": [], "plugins_order": []}
    }

    watcher = audio_home / "bin" / "ee-port-watch"
    assert watcher.stat().st_mode & 0o111  # çalıştırılabilir olmalı
    assert (audio_home / "units" / "ee-port-watch.service").exists()
    assert (audio_home / "autostart" / "easyeffects-service.desktop").exists()

    # kullanıcı servisi: sudo yok, önce daemon-reload; try-restart ise unit
    # dosyası değiştiyse çalışan eski süreci yenisiyle değiştirir
    assert runlog.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "ee-port-watch.service"],
        ["systemctl", "--user", "try-restart", "ee-port-watch.service"],
    ]


def test_audio_dsp_asks_before_installing_on_another_board(audio_home, monkeypatch):
    installs = []
    monkeypatch.setattr(audio_dsp.hardware, "board_matches", lambda q: False)
    monkeypatch.setattr(audio_dsp.prompt, "ask_yes", lambda q: False)
    monkeypatch.setattr(audio_dsp.pacman, "install", lambda r, a: installs.append(r) or 0)

    assert audio_dsp.configure() == 0
    assert installs == []
    assert not (audio_home / "presets").exists()


def test_audio_dsp_stops_when_easyeffects_missing(audio_home, monkeypatch):
    monkeypatch.setattr(audio_dsp.hardware, "board_matches", lambda q: True)
    monkeypatch.setattr(audio_dsp.pacman, "install", lambda r, a: 1)
    monkeypatch.setattr(audio_dsp.pacman, "is_installed", lambda p: False)

    assert audio_dsp.configure() == 1
    assert not (audio_home / "presets").exists()


@pytest.fixture
def coredump_conf(tmp_path, monkeypatch):
    conf_dir = tmp_path / "coredump.conf.d"
    monkeypatch.setattr(coredump, "CONF_DIR", conf_dir)
    monkeypatch.setattr(coredump, "CONF", conf_dir / "99-maxuse.conf")
    return conf_dir


def test_coredump_cap_writes_dropin(coredump_conf, monkeypatch, fake_write, runlog):
    monkeypatch.setattr(coredump, "sudo_write", fake_write)
    monkeypatch.setattr(coredump, "run", runlog)

    assert coredump.configure() == 0

    # sudo tee dizin yaratmaz, önce mkdir -p gerekir
    assert runlog.calls == [["sudo", "mkdir", "-p", str(coredump_conf)]]
    assert (coredump_conf / "99-maxuse.conf").read_text() == "[Coredump]\nMaxUse=1G\n"


def test_coredump_cap_does_not_write_when_mkdir_fails(coredump_conf, monkeypatch):
    written = []
    monkeypatch.setattr(coredump, "sudo_write", lambda p, c: written.append(p) or 0)
    monkeypatch.setattr(coredump, "run", lambda cmd, **kwargs: 1)

    assert coredump.configure() == 1
    assert written == []


def test_board_condition(tmp_path, monkeypatch):
    board = tmp_path / "board_name"
    board.write_text("G513RM\n")
    monkeypatch.setattr(hardware, "BOARD_NAME", board)

    assert hardware.condition_ok("board:g513rm") is True
    assert hardware.condition_ok("board:X13") is False


def test_virt_configure(tmp_path, monkeypatch, fake_write, runlog):
    etc = tmp_path / "etc"
    etc.mkdir()
    for name in ("libvirtd.conf", "qemu.conf", "network.conf"):
        (etc / name).write_text("# stock\n")
    mk = etc / "mkinitcpio.conf"
    mk.write_text("MODULES=()\n")

    monkeypatch.setattr(virt, "run", runlog)
    monkeypatch.setattr(virt, "sudo_write", fake_write)
    monkeypatch.setattr(virt.pacman, "is_installed", lambda p: True)
    monkeypatch.setattr(virt.services, "enable", lambda n: 0)
    monkeypatch.setattr(virt, "_group_exists", lambda g: g == "kvm")
    monkeypatch.setattr(virt.getpass, "getuser", lambda: "drpars")
    monkeypatch.setattr(virt, "LIBVIRTD_CONF", etc / "libvirtd.conf")
    monkeypatch.setattr(virt, "QEMU_CONF", etc / "qemu.conf")
    monkeypatch.setattr(virt, "NETWORK_CONF", etc / "network.conf")
    monkeypatch.setattr(gpuconfig, "sudo_write", fake_write)
    monkeypatch.setattr(gpuconfig, "MKINITCPIO", mk)

    assert virt.configure() == 0
    assert "unix_sock_group = 'libvirt'" in (etc / "libvirtd.conf").read_text()
    assert "MODULES=(virtio virtio_blk virtio_pci virtio_net)" in mk.read_text()
    assert ["sudo", "mkinitcpio", "-P"] in runlog.calls
    start = runlog.calls.index(["sudo", "systemctl", "start", "libvirtd.service"])
    virsh = runlog.calls.index(["sudo", "virsh", "net-autostart", "default"])
    assert start < virsh

    # idempotent
    runlog.calls.clear()
    assert virt.configure() == 0
    assert ["sudo", "mkinitcpio", "-P"] not in runlog.calls


def test_waydroid_zen_vs_dkms(tmp_path, monkeypatch, fake_write):
    installed = {"waydroid", "linux-zen"}
    enables, installs = [], []
    monkeypatch.setattr(waydroid, "sudo_write", fake_write)
    monkeypatch.setattr(waydroid.pacman, "is_installed", lambda p: p in installed)
    monkeypatch.setattr(waydroid.pacman, "install", lambda r, a: installs.append(tuple(a)) or 0)
    monkeypatch.setattr(waydroid.services, "enable", lambda n: enables.append(n) or 0)
    monkeypatch.setattr(waydroid, "MODULES_LOAD", tmp_path / "ml.conf")
    monkeypatch.setattr(waydroid, "MODPROBE", tmp_path / "mp.conf")

    assert waydroid.setup() == 0
    assert not (tmp_path / "mp.conf").exists()  # zen: modül işi yok

    installed.discard("linux-zen")
    assert waydroid.setup() == 0
    assert installs == [("binder_linux-dkms", "python-pyclip")]
    assert (
        (tmp_path / "mp.conf").read_text()
        == "options binder_linux devices=binder,hwbinder,vndbinder\n"
    )
