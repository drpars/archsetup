"""Post-install tasks: dotfiles, sddm, kmscon, network, asus, virt, waydroid."""

import shutil

import pytest

from archsetup.core import asus, dotfiles, gpuconfig, kmscon, network, sddm, virt, waydroid


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

    conf = tmp_path / "pacman.conf"
    conf.write_text("[options]\n[g14]\nServer = x\n")
    monkeypatch.setattr(asus, "PACMAN_CONF", conf)
    asus.install()
    assert "asusctl" in installs[0][0]  # repo'dan

    conf.write_text("[options]\n")
    asus.install()
    assert "asusctl" in installs[1][1]  # AUR'dan
    assert enables == ["power-profiles-daemon"] * 2  # yalnız kurulu paketin servisi


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
