"""Post-install tasks: dotfiles, sddm, kmscon, network, asus, virt, waydroid."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from archsetup.core import (
    asus,
    audio_dsp,
    coding_agents,
    coredump,
    dotfiles,
    i18n,
    iwd,
    hardware,
    gpuconfig,
    kmscon,
    looking_glass,
    network,
    nvidia_laptop,
    sddm,
    sysedit,
    tasks,
    vfio,
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
    (repo / "local" / "share" / "applications").mkdir(parents=True)
    (repo / "local" / "share" / "applications" / "nvim.desktop").write_text("[Desktop Entry]\n")
    (repo / "local" / "share" / "color-schemes").mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()

    monkeypatch.setattr(dotfiles, "DOTFILES_DIR", repo)
    monkeypatch.setattr(
        dotfiles,
        "SECTIONS",
        {
            "config": ("config", home / ".config"),
            "home": ("home", home),
            "local": ("local/share", home / ".local" / "share"),
        },
    )
    monkeypatch.setattr(dotfiles, "_backup_dir", lambda: backup)
    return tmp_path


def test_list_items(dot_env):
    assert dotfiles.list_items("config") == ["kitty"]
    assert dotfiles.list_items("home") == [".zshrc"]


def test_local_section_starts_below_share(dot_env):
    """Ogeler share/ degil, onun icindekiler olmali.

    Tek oge "share" olsaydi onu baglamak ~/.local/share'in tamamini --
    her uygulamanin verisini -- depodaki iki klasorle degistirirdi.
    """
    assert dotfiles.list_items("local") == ["applications", "color-schemes"]
    assert dotfiles.section_target("local").name == "share"


def test_local_items_are_linked_into_local_share(dot_env):
    assert dotfiles.symlink_items("local", ["applications"]) == 0

    linked = dot_env / "home" / ".local" / "share" / "applications"
    assert linked.is_symlink()
    assert (linked / "nvim.desktop").is_file()


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
def test_wallpapers_land_flat_and_spare_local_only_folders(dot_env, monkeypatch):
    """Depo, Resimler dizininin aynasi: kokunde klasorlerin kendisi var.

    Repo kokunu Pictures/Wallpaper'a kopyalamak bu yuzden
    Pictures/Wallpaper/Wallpaper uretiyordu. Ayrica --delete, depoda
    olmayan yerel bir klasoru (ScreenShot) silmemeli.
    """
    wall = dot_env / "wall"
    (wall / ".git").mkdir(parents=True)
    (wall / "Wallpaper").mkdir()
    (wall / "Wallpaper" / "sunset.jpg").write_text("img")
    (wall / "Icons").mkdir()
    (wall / "Icons" / "folder.png").write_text("icon")

    pics = dot_env / "Pictures"
    (pics / "ScreenShot").mkdir(parents=True)
    (pics / "ScreenShot" / "shot.png").write_text("mine")

    monkeypatch.setattr(dotfiles, "WALLPAPER_REPO_DIR", wall)
    monkeypatch.setattr(dotfiles, "ensure_repo", lambda name, target: 0)
    monkeypatch.setattr(dotfiles, "_xdg_dir", lambda name, fb: pics)
    monkeypatch.setattr(dotfiles, "ask_yes", lambda prompt: True)

    assert dotfiles.install_wallpapers() == 0
    assert (pics / "Wallpaper" / "sunset.jpg").exists()
    assert (pics / "Icons" / "folder.png").exists()
    assert not (pics / "Wallpaper" / "Wallpaper").exists()  # ic ice gecmemeli
    assert (pics / "ScreenShot" / "shot.png").exists()  # --delete buraya ulasmamali
    assert not (pics / "Wallpaper" / ".git").exists()


def test_wallpaper_link_is_created_when_missing(dot_env, monkeypatch):
    wall_dir = dot_env / "Pictures" / "Wallpaper"
    wall_dir.mkdir(parents=True)
    (wall_dir / "b.jpg").write_text("img")
    (wall_dir / "a.png").write_text("img")
    link = dot_env / "state" / "wallpaper"
    monkeypatch.setattr(dotfiles, "WALLPAPER_LINK", link)

    assert dotfiles._ensure_wallpaper_link(wall_dir) == 0
    assert link.is_symlink()
    assert Path(os.readlink(link)).name == "a.png"


def test_existing_wallpaper_choice_is_left_alone(dot_env, monkeypatch):
    """Calisan baglanti kullanicinin secimi; wallselect onu yaziyor."""
    wall_dir = dot_env / "Pictures" / "Wallpaper"
    wall_dir.mkdir(parents=True)
    (wall_dir / "a.png").write_text("img")
    (wall_dir / "secilen.jpg").write_text("img")
    link = dot_env / "state" / "wallpaper"
    link.parent.mkdir()
    os.symlink(wall_dir / "secilen.jpg", link)
    monkeypatch.setattr(dotfiles, "WALLPAPER_LINK", link)

    assert dotfiles._ensure_wallpaper_link(wall_dir) == 0
    assert Path(os.readlink(link)).name == "secilen.jpg"


def test_broken_wallpaper_link_is_replaced(dot_env, monkeypatch):
    wall_dir = dot_env / "Pictures" / "Wallpaper"
    wall_dir.mkdir(parents=True)
    (wall_dir / "a.png").write_text("img")
    link = dot_env / "state" / "wallpaper"
    link.parent.mkdir()
    os.symlink(wall_dir / "silinmis.jpg", link)
    monkeypatch.setattr(dotfiles, "WALLPAPER_LINK", link)

    assert dotfiles._ensure_wallpaper_link(wall_dir) == 0
    assert Path(os.readlink(link)).name == "a.png"


def test_wallpapers_refuses_an_unexpected_repo_layout(dot_env, monkeypatch):
    """Kokunde klasor yoksa duz dosyalari Resimler'e sacmak yerine dur."""
    wall = dot_env / "wall"
    wall.mkdir()
    (wall / "sunset.jpg").write_text("img")
    monkeypatch.setattr(dotfiles, "WALLPAPER_REPO_DIR", wall)
    monkeypatch.setattr(dotfiles, "ensure_repo", lambda name, target: 0)
    monkeypatch.setattr(dotfiles, "_xdg_dir", lambda name, fb: dot_env / "Pictures")
    monkeypatch.setattr(dotfiles, "ask_yes", lambda prompt: pytest.fail("sorulmamaliydi"))

    assert dotfiles.install_wallpapers() != 0


def test_sddm_silent(tmp_path, monkeypatch, fake_write):
    monkeypatch.setattr(sddm, "sudo_write", fake_write)
    monkeypatch.setattr(sddm, "_sddm_installed", lambda: True)
    monkeypatch.setattr(sddm.pacman, "install", lambda repo, aur: 0)
    monkeypatch.setattr(sddm, "SDDM_CONF", tmp_path / "sddm.conf")
    monkeypatch.setattr(sddm, "install_avatar", lambda: 0)

    assert sddm.install_silent() == 0
    assert "Current=silent" in (tmp_path / "sddm.conf").read_text()

    monkeypatch.setattr(sddm, "_sddm_installed", lambda: False)
    assert sddm.install_silent() == 1


@pytest.fixture
def avatar_env(tmp_path, monkeypatch, runlog):
    pictures = tmp_path / "Resimler"
    (pictures / "Icons").mkdir(parents=True)
    theme = tmp_path / "themes" / "silent"
    theme.mkdir(parents=True)

    monkeypatch.setattr(sddm.dotfiles, "_xdg_dir", lambda name, fb: pictures)
    monkeypatch.setattr(sddm, "CHANGE_AVATAR", theme / "change_avatar.sh")
    monkeypatch.setattr(sddm, "FACES_DIR", tmp_path / "faces")
    monkeypatch.setattr(sddm, "run", runlog)
    monkeypatch.setattr(sddm.shutil, "which", lambda name: "/usr/bin/mogrify")
    monkeypatch.setattr(sddm, "ask_yes", lambda prompt: True)
    return SimpleNamespace(pictures=pictures, theme=theme, runlog=runlog)


def test_avatar_calls_the_theme_script(avatar_env):
    (avatar_env.pictures / sddm.AVATAR_RELATIVE).write_bytes(b"png")
    (avatar_env.theme / "change_avatar.sh").write_text("#!/bin/bash\n")

    assert sddm.install_avatar() == 0

    called = avatar_env.runlog.calls[-1]
    assert called[0] == "bash"
    assert called[1].endswith("change_avatar.sh")


def test_avatar_without_a_source_is_not_an_error(avatar_env):
    """Kaynak resim kisiye ozel; yoksa kurulum durmaz, avatar kurulmaz."""
    (avatar_env.theme / "change_avatar.sh").write_text("#!/bin/bash\n")

    assert sddm.install_avatar() == 0
    assert avatar_env.runlog.calls == []


def test_avatar_without_the_theme_script_is_not_an_error(avatar_env):
    (avatar_env.pictures / sddm.AVATAR_RELATIVE).write_bytes(b"png")

    assert sddm.install_avatar() == 0
    assert avatar_env.runlog.calls == []


def test_kmscon(tmp_path, monkeypatch, fake_write, runlog):
    services_log = []
    installed = []
    monkeypatch.setattr(kmscon, "sudo_write", fake_write)
    monkeypatch.setattr(kmscon, "run", runlog)
    monkeypatch.setattr(kmscon, "_ask_tty", lambda: 4)
    monkeypatch.setattr(kmscon, "_ask_size", lambda: 18)
    monkeypatch.setattr(kmscon, "_ask_layout", lambda default: default)
    monkeypatch.setattr(kmscon, "_font", lambda: kmscon.FONT)
    monkeypatch.setattr(
        kmscon.pacman, "install", lambda repo, aur: installed.append((repo, aur)) or 0
    )
    monkeypatch.setattr(kmscon.services, "disable", lambda n: services_log.append(("d", n)) or 0)
    monkeypatch.setattr(kmscon.services, "enable", lambda n: services_log.append(("e", n)) or 0)
    monkeypatch.setattr(kmscon, "CONFIG", tmp_path / "kmscon" / "kmscon.conf")

    assert kmscon.install() == 0
    assert ("d", "getty@tty4.service") in services_log
    assert ("e", "kmsconvt@tty4.service") in services_log
    # Düz "kmscon" AUR'dan kalktı; eski ad "target not found" ile döner.
    # check depodan önce gelmeli: kmscon-git onu makedepends'e yazmadığı için
    # AUR yardımcısı kurmaz ve meson "Dependency check not found" ile durur.
    assert installed == [(["check"], ["kmscon-git"])]


def test_kmscon_takes_the_keyboard_from_vconsole(tmp_path, monkeypatch):
    vconsole = tmp_path / "vconsole.conf"
    vconsole.write_text('KEYMAP=trq\nFONT=ter-v22b\nXKBLAYOUT="tr"\nXKBMODEL=pc105\n')
    monkeypatch.setattr(kmscon, "VCONSOLE", vconsole)

    keys = kmscon.keyboard()
    assert keys["xkb-layout"] == "tr"
    assert keys["xkb-model"] == "pc105"

    conf = kmscon.build_config(16, kmscon.FONT, keys)
    # kmscon sistem klavyesini okumaz: xkb-layout yazılmazsa libxkbcommon
    # "us" düzenine düşer ve Türkçe VT'li makinede konsol İngilizce olur.
    assert "xkb-layout=tr" in conf
    # Bilinmeyen anahtar ölümcül değil ama her açılışta hata kaydı düşer.
    assert "font-dpi" not in conf


def test_kmscon_keyboard_falls_back_without_vconsole(tmp_path, monkeypatch):
    monkeypatch.setattr(kmscon, "VCONSOLE", tmp_path / "yok.conf")
    assert kmscon.keyboard()["xkb-layout"] == kmscon.DEFAULT_LAYOUT


@pytest.fixture
def samba_env(tmp_path, monkeypatch, fake_write, runlog):
    """network.configure() with everything privileged stubbed out."""
    enables, disables = [], []
    monkeypatch.setattr(network, "run", runlog)
    monkeypatch.setattr(network, "sudo_write", fake_write)
    monkeypatch.setattr(network.pacman, "install", lambda repo, aur: 0)
    monkeypatch.setattr(network.pacman, "is_installed", lambda p: True)
    monkeypatch.setattr(network.services, "enable", lambda n: enables.append(n) or 0)
    monkeypatch.setattr(network.services, "disable", lambda n: disables.append(n) or 0)
    monkeypatch.setattr(network, "_group_exists", lambda n: False)
    monkeypatch.setattr(network.getpass, "getuser", lambda: "drpars")
    monkeypatch.setattr(network.shutil, "which", lambda n: None)
    monkeypatch.setattr(network, "SMB_CONF", tmp_path / "smb.conf")
    # Drop-in ve unit varligi gercek makineden okunmamali; yoksa test
    # calistigi makineye gore farkli dala girer.
    monkeypatch.setattr(network, "WAIT_ONLINE_DROPIN", tmp_path / "any.conf")
    monkeypatch.setattr(network.services, "unit_exists", lambda n: False)
    return SimpleNamespace(enables=enables, disables=disables, runlog=runlog)


def test_network_group_before_chown(tmp_path, monkeypatch, samba_env):
    monkeypatch.setattr(network.prompt, "ask_yes", lambda q: True)

    assert network.configure() == 0
    conf = (tmp_path / "smb.conf").read_text()
    assert "log file = /var/log/samba/%m.log" in conf
    calls = samba_env.runlog.calls
    assert calls.index(["sudo", "groupadd", "-r", "sambashare"]) < calls.index(
        ["sudo", "chown", "root:sambashare", network.USERSHARES]
    )
    assert samba_env.enables == ["smb", "nmb", "avahi-daemon.service"]
    assert samba_env.disables == []


def test_samba_boot_answer_decides_enable_or_disable(monkeypatch, samba_env):
    """Saying no keeps smb/nmb off network-online.target's critical path."""
    monkeypatch.setattr(network.prompt, "ask_yes", lambda q: False)

    assert network.configure() == 0
    assert samba_env.disables == ["smb", "nmb"]
    assert samba_env.enables == ["avahi-daemon.service"]
    # Still restarted, so the shares work right now without a reboot.
    assert ["sudo", "systemctl", "restart", "smb.service", "nmb.service"] in (
        samba_env.runlog.calls
    )


def test_wait_online_dropin(tmp_path, monkeypatch, fake_write, runlog):
    dropin = tmp_path / "wait-online.d" / "any.conf"
    monkeypatch.setattr(network, "run", runlog)
    monkeypatch.setattr(network, "sudo_write", fake_write)
    monkeypatch.setattr(network.services, "unit_exists", lambda n: True)
    monkeypatch.setattr(network, "WAIT_ONLINE_DROPIN", dropin)

    assert network.wait_online_timeout() == 0
    text = dropin.read_text()
    # ExecStart must be cleared first; systemd appends otherwise.
    assert "ExecStart=\nExecStart=/usr/lib/systemd/systemd-networkd-wait-online" in text
    assert "--any --timeout=3" in text
    assert ["sudo", "systemctl", "daemon-reload"] in runlog.calls
    # The unit is throttled, never disabled: smb/nmb and the keyring sync are
    # ordered after network-online.target.
    assert not any("disable" in call for call in runlog.calls)


def test_wait_online_skipped_without_networkd(tmp_path, monkeypatch, runlog):
    written = []
    monkeypatch.setattr(network, "run", runlog)
    monkeypatch.setattr(network, "sudo_write", lambda p, c: written.append(p) or 0)
    monkeypatch.setattr(network.services, "unit_exists", lambda n: False)

    assert network.wait_online_timeout() == 0
    assert written == []
    assert runlog.calls == []


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
    monkeypatch.setattr(sysedit, "run", runlog)
    monkeypatch.setattr(sysedit, "sudo_write", fake_write)
    monkeypatch.setattr(nvidia_laptop.hardware, "gpu_matches", lambda q: True)
    # Sasi gercek makineden okunmamali: CI konteynerinde hostnamectl yok,
    # orada gorev "bilinmiyor" dalina dusup soru sorardi.
    monkeypatch.setattr(nvidia_laptop.hardware, "is_laptop", lambda: True)
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
    assert enabled == [
        *nvidia_laptop.SLEEP_SERVICES,
        nvidia_laptop.S2H_SERVICE,
        nvidia_laptop.POWERD_SERVICE,
    ]
    assert ["sudo", "mkinitcpio", "-P"] in runlog.calls
    assert ["sudo", "udevadm", "control", "--reload-rules"] in runlog.calls


def test_nvidia_laptop_turing_adds_firmware_option(monkeypatch):
    monkeypatch.setattr(
        nvidia_laptop.hardware,
        "nvidia_gpu_lines",
        lambda: ["01:00.0 VGA compatible controller: NVIDIA Corporation TU106M [RTX 2060]"],
    )
    assert "NVreg_EnableGpuFirmware=0" in nvidia_laptop.modprobe_content()


def test_write_with_backup_backs_up_differing_file(tmp_path, monkeypatch, fake_write, runlog):
    modprobe = tmp_path / "nvidia.conf"
    modprobe.write_text("options nvidia_drm modeset=1 fbdev=1\n")
    monkeypatch.setattr(sysedit, "run", runlog)
    monkeypatch.setattr(sysedit, "sudo_write", fake_write)

    rc, changed = sysedit.write_with_backup(modprobe, "new content\n")
    assert (rc, changed) == (0, True)
    assert ["sudo", "cp", str(modprobe), f"{modprobe}.bak"] in runlog.calls

    # İkinci çağrı: içerik aynı, yazma da yedekleme de yok
    runlog.calls.clear()
    assert sysedit.write_with_backup(modprobe, "new content\n") == (0, False)
    assert runlog.calls == []


@pytest.fixture
def drm_sysfs(tmp_path, monkeypatch):
    """Fake /sys/class/drm. Card numbers are deliberately not 0 and 1."""

    def build(cards):
        root = tmp_path / "drm"
        root.mkdir(exist_ok=True)
        for name, vendor, slot in cards:
            device = root / name / "device"
            device.mkdir(parents=True)
            (device / "vendor").write_text(f"{vendor}\n")
            (device / "uevent").write_text(
                f"DRIVER=x\nPCI_SLOT_NAME={slot}\nPCI_ID=x\n"
            )
            # Connector entries live next to the cards and have no device node;
            # a glob that swallowed them would produce duplicate matches.
            (root / f"{name}-DP-1").mkdir()
        monkeypatch.setattr(vfio, "DRM_CLASS", root)
        return root

    return build


def test_vfio_cards_reads_slots_and_skips_connectors(drm_sysfs):
    drm_sysfs(
        [("card1", "0x10de", "0000:01:00.0"), ("card2", "0x1002", "0000:05:00.0")]
    )
    assert vfio.cards() == [
        ("card1", "0x10de", "0000:01:00.0"),
        ("card2", "0x1002", "0000:05:00.0"),
    ]


def test_vfio_writes_rule_for_detected_igpu(
    tmp_path, monkeypatch, drm_sysfs, fake_write, runlog
):
    drm_sysfs(
        [("card1", "0x10de", "0000:01:00.0"), ("card2", "0x1002", "0000:05:00.0")]
    )
    rules = tmp_path / "70-vfio-igpu.rules"
    link = tmp_path / "amd-igpu"
    link.symlink_to(tmp_path / "card2")
    monkeypatch.setattr(vfio, "UDEV_RULES", rules)
    monkeypatch.setattr(vfio, "DEV_LINK", link)
    monkeypatch.setattr(vfio, "run", runlog)
    monkeypatch.setattr(sysedit, "run", runlog)
    monkeypatch.setattr(sysedit, "sudo_write", fake_write)

    assert vfio.install_udev_rule() == 0
    rule = rules.read_text()
    # PCI adresi makineden okunur; kural ne kart numarasi ne de ":" tasiyan
    # bir yol icermeli -- AQ_DRM_DEVICES listeyi ":" ile ayiriyor.
    assert 'KERNELS=="0000:05:00.0"' in rule
    assert 'SYMLINK+="dri/amd-igpu"' in rule
    assert "0000:01:00.0" not in rule
    assert ":" not in vfio.SYMLINK
    assert ["sudo", "udevadm", "control", "--reload-rules"] in runlog.calls
    assert [
        "sudo",
        "udevadm",
        "trigger",
        "--settle",
        "--subsystem-match=drm",
    ] in runlog.calls


def test_vfio_refuses_machine_without_igpu(tmp_path, monkeypatch, drm_sysfs):
    """Masaustunde (tek NVIDIA karti) kural yazilmaz -- baglanacak kart yok."""
    drm_sysfs([("card1", "0x10de", "0000:01:00.0")])
    rules = tmp_path / "70-vfio-igpu.rules"
    monkeypatch.setattr(vfio, "UDEV_RULES", rules)

    assert vfio.install_udev_rule() == 1
    assert not rules.exists()


def test_vfio_reports_failure_when_symlink_does_not_appear(
    tmp_path, monkeypatch, drm_sysfs, fake_write, runlog
):
    drm_sysfs(
        [("card1", "0x10de", "0000:01:00.0"), ("card2", "0x1002", "0000:05:00.0")]
    )
    rules = tmp_path / "70-vfio-igpu.rules"
    monkeypatch.setattr(vfio, "UDEV_RULES", rules)
    monkeypatch.setattr(vfio, "DEV_LINK", tmp_path / "yok")
    monkeypatch.setattr(vfio, "run", runlog)
    monkeypatch.setattr(sysedit, "run", runlog)
    monkeypatch.setattr(sysedit, "sudo_write", fake_write)

    # Kural diske yazildi ama udev uygulamadi: "tamam" demek yalan olurdu.
    assert vfio.install_udev_rule() != 0
    assert rules.exists()


@pytest.fixture
def pci_sysfs(tmp_path, monkeypatch):
    """Fake /sys/bus/pci/devices, including the iommu_group symlink."""

    def build(devices, groups):
        root = tmp_path / "pci"
        root.mkdir(exist_ok=True)
        group_root = tmp_path / "iommu_groups"
        for number, members in groups.items():
            members_dir = group_root / str(number) / "devices"
            members_dir.mkdir(parents=True)
            for member in members:
                (members_dir / member).mkdir()
        for slot, (vendor, klass, group) in devices.items():
            device = root / slot
            device.mkdir()
            (device / "vendor").write_text(f"{vendor}\n")
            (device / "class").write_text(f"{klass}\n")
            if group is not None:
                (device / "iommu_group").symlink_to(group_root / str(group))
        monkeypatch.setattr(vfio, "PCI_DEVICES", root)
        return root

    return build


@pytest.fixture
def hook_paths(tmp_path, monkeypatch, fake_write, runlog):
    """Point the handover-hook task at tmp_path and stub out libvirt/sudo."""
    hook = tmp_path / "hooks" / "qemu.d" / vfio.HOOK_NAME
    conf = tmp_path / "hooks" / "vfio.conf"
    monkeypatch.setattr(vfio, "HOOK_DIR", hook.parent)
    monkeypatch.setattr(vfio, "HOOK", hook)
    monkeypatch.setattr(vfio, "HOOK_BACKUP", hook.parent.parent / (hook.name + ".bak"))
    monkeypatch.setattr(vfio, "VFIO_CONF", conf)
    monkeypatch.setattr(vfio.pacman, "is_installed", lambda name: True)
    monkeypatch.setattr(vfio, "run", runlog)
    monkeypatch.setattr(sysedit, "run", runlog)
    monkeypatch.setattr(sysedit, "sudo_write", fake_write)
    return SimpleNamespace(hook=hook, conf=conf, calls=runlog.calls)


# The card's own functions: exactly what this laptop's group 13 holds.
DGPU_GROUP = {
    "0000:01:00.0": ("0x10de", "0x030000", 13),
    "0000:01:00.1": ("0x10de", "0x040300", 13),
    "0000:05:00.0": ("0x1002", "0x030000", 20),
}


def test_vfio_hook_writes_devices_read_off_the_machine(pci_sysfs, hook_paths):
    pci_sysfs(
        DGPU_GROUP,
        {13: ["0000:01:00.0", "0000:01:00.1"], 20: ["0000:05:00.0"]},
    )

    assert vfio.install_handover_hook() == 0

    # Both functions of the card, and only those: the audio function has to
    # move with the VGA one or the guest gets half a device.
    conf = hook_paths.conf.read_text()
    assert 'VFIO_DEVICES="0000:01:00.0 0000:01:00.1"' in conf
    assert "0000:05:00.0" not in conf

    assert hook_paths.hook.read_text() == vfio.HOOK_ASSET.read_text()
    # libvirt skips a non-executable hook without a word, and reads its hook
    # directory only at startup -- both have to be handled by the install.
    assert ["sudo", "chmod", "0755", str(hook_paths.hook)] in hook_paths.calls
    assert [
        "sudo",
        "systemctl",
        "try-restart",
        "libvirtd.service",
    ] in hook_paths.calls


def test_vfio_hook_backup_lands_outside_the_dropin_directory(pci_sysfs, hook_paths):
    """A sibling .bak would be a second hook, and the older one at that.

    libvirt runs every executable file in qemu.d/ "with any name", and the copy
    is taken with `cp`, execute bit included. On 2026-08-04 the version being
    replaced was the one that wedges the machine, so the backup would have
    re-run it on every VM start.
    """
    pci_sysfs(
        DGPU_GROUP,
        {13: ["0000:01:00.0", "0000:01:00.1"], 20: ["0000:05:00.0"]},
    )
    assert vfio.install_handover_hook() == 0
    hook_paths.hook.write_text("# an older hook someone is replacing\n")
    hook_paths.calls.clear()

    assert vfio.install_handover_hook() == 0

    copies = [call for call in hook_paths.calls if call[:2] == ["sudo", "cp"]]
    assert len(copies) == 1
    assert Path(copies[0][2]) == hook_paths.hook
    assert Path(copies[0][3]).parent != hook_paths.hook.parent


def test_vfio_hook_is_idempotent_and_skips_the_restart(pci_sysfs, hook_paths):
    pci_sysfs(
        DGPU_GROUP,
        {13: ["0000:01:00.0", "0000:01:00.1"], 20: ["0000:05:00.0"]},
    )
    assert vfio.install_handover_hook() == 0
    hook_paths.hook.chmod(0o755)
    hook_paths.calls.clear()

    assert vfio.install_handover_hook() == 0
    # Nothing moved, so nothing that costs anything runs: no restart of a
    # daemon that may be serving a running VM, no rewrite.
    assert ["sudo", "systemctl", "try-restart", "libvirtd.service"] not in (
        hook_paths.calls
    )
    assert ["sudo", "chmod", "0755", str(hook_paths.hook)] not in hook_paths.calls


def test_vfio_hook_refuses_a_shared_iommu_group(pci_sysfs, hook_paths):
    """Grupta yabanci aygit varsa passthrough zaten calismaz -- yazma."""
    devices = dict(DGPU_GROUP)
    devices["0000:02:00.0"] = ("0x1022", "0x0c0330", 13)  # a USB controller
    pci_sysfs(
        devices,
        {
            13: ["0000:01:00.0", "0000:01:00.1", "0000:02:00.0"],
            20: ["0000:05:00.0"],
        },
    )

    assert vfio.install_handover_hook() == 1
    assert not hook_paths.conf.exists()
    assert not hook_paths.hook.exists()


# libvirt's own commented sample, copied verbatim from /etc/libvirt/qemu.conf.
# Uncommenting *this* is the point: naming the key replaces libvirt's built-in
# list, so a list written from memory would quietly take /dev/null and friends
# away from every VM on the machine.
QEMU_CONF_SAMPLE = """\
# This is the basic set of devices allowed / required by
# all virtual machines.
#
#cgroup_device_acl = [
#    "/dev/null", "/dev/full", "/dev/zero",
#    "/dev/random", "/dev/urandom",
#    "/dev/ptmx", "/dev/userfaultfd"
#]
#

# Some other setting
#nvram = []
"""


@pytest.mark.parametrize(
    "resolution,expected",
    # The four rows of the "Common Values" table in the Looking Glass B7 docs.
    # A typo in the formula stays invisible otherwise: every wrong answer is
    # still a plausible-looking power of two.
    [((1920, 1080), 32), ((1920, 1200), 32), ((2560, 1440), 64), ((3840, 2160), 128)],
)
def test_lg_size_matches_the_documented_table(resolution, expected):
    assert looking_glass.required_mb(*resolution) == expected


@pytest.fixture
def drm_connectors(tmp_path, monkeypatch):
    """Fake /sys/class/drm connectors with status and mode lists."""

    def build(connectors):
        root = tmp_path / "drm-connectors"
        root.mkdir(exist_ok=True)
        for name, status, modes in connectors:
            connector = root / name
            connector.mkdir()
            (connector / "status").write_text(f"{status}\n")
            (connector / "modes").write_text("".join(f"{m}\n" for m in modes))
        monkeypatch.setattr(looking_glass, "DRM_CLASS", root)
        return root

    return build


def test_lg_reads_the_preferred_mode_of_connected_outputs_only(drm_connectors):
    drm_connectors(
        [
            # First line is the preferred mode; the rest are fallbacks nobody
            # will run the guest at.
            ("card1-eDP-1", "connected", ["2560x1440", "1920x1080"]),
            # A disconnected 4K output would otherwise buy 128 MB for a screen
            # that is not there.
            ("card1-DP-2", "disconnected", ["3840x2160"]),
        ]
    )
    assert looking_glass.connected_modes() == [(2560, 1440)]
    assert looking_glass.largest_mode() == (2560, 1440)


def test_lg_acl_uncomments_libvirt_own_sample(tmp_path):
    updated = looking_glass.acl_with_kvmfr(QEMU_CONF_SAMPLE)
    assert updated is not None
    assert 'cgroup_device_acl = [\n    "/dev/kvmfr0",\n' in updated
    # Everything libvirt shipped in the sample survives -- that is the whole
    # reason for editing rather than generating the block.
    for device in ("/dev/null", "/dev/full", "/dev/zero", "/dev/userfaultfd"):
        assert device in updated
    assert "#cgroup_device_acl" not in updated
    assert looking_glass.acl_allows_kvmfr(QEMU_CONF_SAMPLE) is False
    assert looking_glass.acl_allows_kvmfr(updated) is True


def test_lg_acl_refuses_an_active_key_it_cannot_parse():
    """Ayrıştırılamayan etkin anahtara ikincisi eklenmez."""
    text = 'cgroup_device_acl = ["/dev/null", "/dev/kvm"]\n' + QEMU_CONF_SAMPLE
    # Uncommenting the sample here would leave two active assignments and let
    # file order decide the result -- the mkinitcpio PRESETS bug again.
    assert looking_glass.acl_with_kvmfr(text) is None
    assert looking_glass.acl_allows_kvmfr(text) is False


def test_lg_client_release_drops_epoch_and_pkgrel(monkeypatch):
    """Misafir tarafının indirme adresi kurulu sürümden türetiliyor."""
    monkeypatch.setattr(
        looking_glass.pacman, "query", lambda cmd: ["looking-glass", "2:B7-7"]
    )
    assert looking_glass.client_release() == "B7"


@pytest.fixture
def lg_paths(tmp_path, monkeypatch, fake_write, runlog, drm_connectors):
    """Point the Looking Glass task at tmp_path and stub out sudo/pacman."""
    drm_connectors([("card1-eDP-1", "connected", ["2560x1440"])])
    paths = SimpleNamespace(
        modprobe=tmp_path / "modprobe.d" / "kvmfr.conf",
        modules_load=tmp_path / "modules-load.d" / "kvmfr.conf",
        rules=tmp_path / "rules.d" / "99-kvmfr.rules",
        qemu_conf=tmp_path / "qemu.conf",
        node=tmp_path / "kvmfr0",
        calls=runlog.calls,
        installed=[],
    )
    paths.qemu_conf.write_text(QEMU_CONF_SAMPLE)
    monkeypatch.setattr(looking_glass, "MODPROBE", paths.modprobe)
    monkeypatch.setattr(looking_glass, "MODULES_LOAD", paths.modules_load)
    monkeypatch.setattr(looking_glass, "UDEV_RULES", paths.rules)
    monkeypatch.setattr(looking_glass, "QEMU_CONF", paths.qemu_conf)
    monkeypatch.setattr(looking_glass, "DEV_NODE", paths.node)
    monkeypatch.setattr(looking_glass, "loaded_size_mb", lambda: None)
    monkeypatch.setattr(looking_glass, "run", runlog)
    monkeypatch.setattr(looking_glass.pacman, "is_installed", lambda name: True)
    monkeypatch.setattr(looking_glass.pacman, "query", lambda cmd: [])
    monkeypatch.setattr(
        looking_glass,
        "kernel_headers",
        lambda: ("linux-g14-headers", True),
    )
    monkeypatch.setattr(
        looking_glass.pacman,
        "install",
        lambda repo, aur: paths.installed.extend(repo + aur) or 0,
    )
    monkeypatch.setattr(sysedit, "run", runlog)
    monkeypatch.setattr(sysedit, "sudo_write", fake_write)
    # Headless: the size prompt takes the calculated value.
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    return paths


def test_lg_writes_the_four_things_the_packages_leave_out(lg_paths):
    # Non-zero because no real module can create /dev/kvmfr0 under tmp_path;
    # what is being checked here is what landed on disk on the way there.
    assert looking_glass.install() != 0

    assert lg_paths.installed == [
        "looking-glass",
        "looking-glass-module-dkms",
    ]
    # 2560x1440 -> 64 MB, read off the machine rather than baked into a file.
    assert "options kvmfr static_size_mb=64" in lg_paths.modprobe.read_text()
    assert "2560x1440" in lg_paths.modprobe.read_text()
    assert lg_paths.modules_load.read_text().rstrip().endswith("kvmfr")

    rule = lg_paths.rules.read_text()
    assert 'SUBSYSTEM=="kvmfr"' in rule
    assert 'GROUP="kvm", MODE="0660"' in rule
    assert ["sudo", "udevadm", "control", "--reload-rules"] in lg_paths.calls

    # And the one qemu would still be denied by even with the rule in place.
    assert looking_glass.acl_allows_kvmfr(lg_paths.qemu_conf.read_text())
    assert [
        "sudo",
        "systemctl",
        "try-restart",
        "libvirtd.service",
    ] in lg_paths.calls


def test_lg_refuses_when_qemu_left_a_plain_file_at_the_node(lg_paths):
    """QEMU, modül yüklenmeden VM açılırsa /dev/kvmfr0'ı düz dosya yapar."""
    lg_paths.node.write_text("")

    assert looking_glass.install() == 1
    # Nothing is written on top of a broken node: the fix is deleting it, and
    # a task that "succeeded" would hide that.
    assert not lg_paths.modprobe.exists()
    assert not lg_paths.rules.exists()


def test_lg_getsize_ioctl_matches_the_module_header():
    """_IO('u', 0x44), module/kvmfr.h.

    Bir harf hatası istisna atmaz: boyut sonsuza dek "bilinmiyor" döner ve
    aşağıdaki denetim bir kez bile çalışmaz. Canlı cihaza karşı doğrulandı.
    """
    assert looking_glass.KVMFR_GETSIZE == 0x7544


def test_lg_says_a_loaded_module_will_not_take_the_new_size(lg_paths, monkeypatch):
    """Yüklü modül parametreyi yeniden okumaz; 'tamam' demek yalan olur."""
    monkeypatch.setattr(looking_glass, "loaded_size_mb", lambda: 32)

    assert looking_glass.install() != 0
    assert "static_size_mb=64" in lg_paths.modprobe.read_text()
    # No modprobe attempt: it would return 0 and change nothing.
    assert ["sudo", "modprobe", "kvmfr"] not in lg_paths.calls


def test_lg_is_quiet_when_the_loaded_size_is_already_right(lg_paths, monkeypatch):
    monkeypatch.setattr(looking_glass, "loaded_size_mb", lambda: 64)
    # A test tree cannot hold a real character device; what matters is that
    # both places asking about the node get the same answer.
    monkeypatch.setattr(looking_glass, "node_is_device", lambda: True)

    assert looking_glass.install() == 0
    assert ["sudo", "modprobe", "kvmfr"] not in lg_paths.calls


def test_lg_leaves_an_already_allowed_acl_alone(lg_paths):
    lg_paths.qemu_conf.write_text(
        looking_glass.acl_with_kvmfr(QEMU_CONF_SAMPLE) or ""
    )
    before = lg_paths.qemu_conf.read_text()

    looking_glass.install()

    assert lg_paths.qemu_conf.read_text() == before
    assert [
        "sudo",
        "systemctl",
        "try-restart",
        "libvirtd.service",
    ] not in lg_paths.calls


def test_vfio_hook_refuses_without_an_iommu_group(pci_sysfs, hook_paths):
    pci_sysfs({"0000:01:00.0": ("0x10de", "0x030000", None)}, {})

    assert vfio.install_handover_hook() == 1
    assert not hook_paths.conf.exists()


def test_vfio_hook_refuses_machine_without_dgpu(pci_sysfs, hook_paths):
    pci_sysfs({"0000:05:00.0": ("0x1002", "0x030000", 20)}, {20: ["0000:05:00.0"]})

    assert vfio.install_handover_hook() == 1
    assert not hook_paths.conf.exists()


def test_vfio_hook_needs_libvirt(pci_sysfs, hook_paths, monkeypatch):
    pci_sysfs(DGPU_GROUP, {13: ["0000:01:00.0", "0000:01:00.1"]})
    monkeypatch.setattr(vfio.pacman, "is_installed", lambda name: False)

    assert vfio.install_handover_hook() == 1
    assert not hook_paths.hook.exists()


# --- the shell gate --------------------------------------------------------
#
# The hook decides whether to touch the driver binding by reading the domain
# XML libvirt puts on its stdin. That decision is the one part of this work
# that cannot be checked by reading it: a gate that never fires is
# indistinguishable from a hook that was never installed. So it is exercised
# for real, with bash, against XML shaped like libvirt's own output.

CLAIMING_DOMAIN = """\
<domain type='kvm'>
  <name>win11</name>
  <devices>
    <hostdev mode='subsystem' type='pci' managed='yes'>
      <source>
        <address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
      </source>
      <address type='pci' domain='0x0000' bus='0x05' slot='0x00' function='0x0'/>
    </hostdev>
  </devices>
</domain>
"""

# The trap: the *guest* address of an unrelated device sits at 01:00.0 while
# the only card actually asked for is a different one. Matching addresses
# anywhere in the XML fires here -- and that is a handover for a guest that
# never wanted the card. The wrapped attributes are copied from libvirt's own
# manual, which splits a long <address/> across lines.
DECOY_DOMAIN = """\
<domain type='kvm'>
  <devices>
    <disk type='file' device='disk'>
      <address type='pci' domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
    </disk>
    <hostdev mode='subsystem' type='pci' managed='yes'>
      <source writeFiltering='no'>
        <address domain='0x0000' bus='0x06' slot='0x12' function='0x1'/>
      </source>
      <address type='pci' domain='0x0000' bus='0x00'
               slot='0x02' function='0x0'/>
    </hostdev>
  </devices>
</domain>
"""

WRAPPED_DOMAIN = """\
<domain type='kvm'><devices><hostdev mode='subsystem'
  type='pci' managed='yes'><source><address domain='0x0000'
  bus='0x1' slot='0x0'
  function='0x1'/></source></hostdev></devices></domain>
"""


def _run_gate(tmp_path, xml, devices='"0000:01:00.0 0000:01:00.1"'):
    conf = tmp_path / "vfio.conf"
    conf.write_text(f"VFIO_DEVICES={devices}\n")
    return subprocess.run(
        ["bash", str(vfio.HOOK_ASSET)],
        input=xml,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "VFIO_CONF": str(conf),
            "VFIO_HOOK_CHECK": "1",
        },
    )


def test_hook_gate_fires_for_a_guest_that_asks_for_the_card(tmp_path):
    out = _run_gate(tmp_path, CLAIMING_DOMAIN)
    assert out.returncode == 0
    assert "0000:01:00.0" in out.stdout


def test_hook_gate_ignores_a_guest_side_address(tmp_path):
    out = _run_gate(tmp_path, DECOY_DOMAIN)
    assert out.returncode == 1
    # Only the host address of the hostdev source counts.
    assert "0000:06:12.1" in out.stdout


def test_hook_gate_reads_attributes_split_across_lines(tmp_path):
    out = _run_gate(tmp_path, WRAPPED_DOMAIN)
    assert out.returncode == 0
    # Short hex forms too: libvirt writes 0x1, the config says 01.
    assert "0000:01:00.1" in out.stdout


def test_hook_refuses_a_malformed_config(tmp_path):
    out = _run_gate(tmp_path, CLAIMING_DOMAIN, devices='"not-an-address"')
    assert out.returncode == 1
    assert "not a PCI address" in out.stderr


# --- the greeter's X server ------------------------------------------------


@pytest.fixture
def xorg_paths(tmp_path, monkeypatch, fake_write, runlog):
    """Point the AutoAddGPU task at tmp_path, with no X11 session defined."""
    conf = tmp_path / "xorg.conf.d" / "20-vfio-no-autoaddgpu.conf"
    monkeypatch.setattr(vfio, "XORG_CONF_DIR", conf.parent)
    monkeypatch.setattr(vfio, "XORG_AUTOADDGPU", conf)
    monkeypatch.setattr(vfio, "XSESSIONS", tmp_path / "xsessions")
    monkeypatch.setattr(vfio, "XORG_LOG", tmp_path / "Xorg.0.log")
    monkeypatch.setattr(vfio, "run", runlog)
    monkeypatch.setattr(sysedit, "run", runlog)
    monkeypatch.setattr(sysedit, "sudo_write", fake_write)
    return SimpleNamespace(
        conf=conf,
        sessions=tmp_path / "xsessions",
        log=tmp_path / "Xorg.0.log",
        calls=runlog.calls,
    )


HYBRID_CARDS = [
    ("card1", "0x10de", "0000:01:00.0"),
    ("card2", "0x1002", "0000:05:00.0"),
]


def test_xorg_autoaddgpu_turns_the_udev_backend_off(drm_sysfs, xorg_paths):
    drm_sysfs(HYBRID_CARDS)

    assert vfio.disable_xorg_autoaddgpu() == 0

    conf = xorg_paths.conf.read_text()
    assert 'Option "AutoAddGPU" "off"' in conf
    assert 'Section "ServerFlags"' in conf


def test_xorg_autoaddgpu_refuses_where_x11_sessions_exist(drm_sysfs, xorg_paths):
    """The setting is machine-wide; no Xorg flag exists that could scope it.

    On a machine that really runs X11 desktops, turning the udev backend off
    takes their PRIME offload outputs with it. This laptop is the opposite
    case -- /usr/share/xsessions does not exist at all -- and that difference
    is the only thing standing between "narrow fix" and "broke the desktop".
    """
    drm_sysfs(HYBRID_CARDS)
    xorg_paths.sessions.mkdir()
    (xorg_paths.sessions / "plasmax11.desktop").write_text("[Desktop Entry]\n")

    assert vfio.disable_xorg_autoaddgpu() == 1
    assert not xorg_paths.conf.exists()


def test_xorg_autoaddgpu_refuses_a_single_gpu_machine(drm_sysfs, xorg_paths):
    # Nothing to hand over, and nothing to fall back to: same structural no
    # the symlink task gives.
    drm_sysfs([("card1", "0x1002", "0000:05:00.0")])

    assert vfio.disable_xorg_autoaddgpu() == 1
    assert not xorg_paths.conf.exists()


def test_xorg_autoaddgpu_does_not_call_the_running_server_done(
    drm_sysfs, xorg_paths, capsys
):
    """A GPU screen in the log means the card is still held, file or no file.

    The server that is up was started before the file existed, so writing it
    changes nothing until the next one. Saying "done" here is how the
    expensive experiment gets run against a host that never let go.
    """
    drm_sysfs(HYBRID_CARDS)
    xorg_paths.log.write_text("(II) NVIDIA(G0): Using...\n(II) NVIDIA(G0): more\n")

    assert vfio.disable_xorg_autoaddgpu() == 0

    expected = i18n.t("vfio.xorg_pending", path=xorg_paths.conf, count=2)
    assert expected in capsys.readouterr().out


def test_xorg_autoaddgpu_reports_a_server_that_left_the_card_alone(
    drm_sysfs, xorg_paths, capsys
):
    drm_sysfs(HYBRID_CARDS)
    # The primary screen is the integrated GPU, and it stays: NVIDIA(0)
    # without the G would be a screen on the card itself, which is not what
    # this counts either.
    xorg_paths.log.write_text("(II) AMDGPU(0): Using...\n")

    assert vfio.disable_xorg_autoaddgpu() == 0

    expected = i18n.t("vfio.xorg_effective", path=xorg_paths.conf)
    assert expected in capsys.readouterr().out


# --- the bind order --------------------------------------------------------
#
# The order in which the hook writes to sysfs is the rest of what cannot be
# checked by reading, and it is the expensive part to get wrong: writing an
# unbind while the nvidia modules are loaded parks a kernel task in state R
# that no signal clears, and takes libvirtd down with it. It has cost three
# reboots, once from this very script (2026-08-04).
#
# So the kernel side is faked just far enough to answer the questions that
# matter -- what was written, in what order, and what the driver binding looked
# like at each step -- and the hook is run for real against it.

DGPU = "0000:01:00.0"
DGPU_AUDIO = "0000:01:00.1"

LSMOD_FULL = """\
nvidia_drm            167936  2
nvidia_uvm           2449408  0
nvidia_modeset       1921024  3 nvidia_drm
nvidia              18186240  45 nvidia_uvm,nvidia_modeset
"""

# Records every write and, at each modprobe, what the override said at that
# moment -- which is exactly the ordering claim under test. "-r" drops the
# nvidia driver links the way removing the module really does, unless the
# regrab flag is set: that flag is udev getting there first.
STUB_MODPROBE = """\
#!/bin/sh
printf 'modprobe %s\\n' "$*" >> "$TRACE"
printf 'override-was %s\\n' \
    "$(cat "$SYSFS_PCI/devices/0000:01:00.0/driver_override")" >> "$TRACE"
if [ "$1" = "-r" ]; then
    shift
    : > "$STATE/lsmod"
    if [ ! -f "$STATE/regrab" ]; then
        rm -f "$SYSFS_PCI/devices/0000:01:00.0/driver"
        rm -rf "$SYSFS_PCI/drivers/nvidia"
    fi
    # "in use" still clears lsmod, because that is what was measured: the
    # kernel killed the process holding the card, so a second later the
    # modules really were gone -- and the session with them.
    if [ -f "$STATE/in-use" ]; then
        printf 'FATAL: Module %s is in use.\\n' "$1" >&2
        exit 1
    fi
    exit 0
fi
if [ "$1" = "nvidia_drm" ]; then
    mkdir -p "$SYSFS_PCI/drivers/nvidia"
    cp "$STATE/lsmod.full" "$STATE/lsmod"
else
    mkdir -p "$SYSFS_PCI/drivers/$1"
fi
exit 0
"""

STUB_LSMOD = """\
#!/bin/sh
echo "Module                  Size  Used by"
cat "$STATE/lsmod" 2>/dev/null
exit 0
"""

# The mini-kernel: drivers_probe binds whatever the override names, unbind
# detaches. Only bind_vfio goes through tee, which is the path under test.
STUB_TEE = """\
#!/bin/sh
target=$1
dev=$(cat)
printf 'write %s %s\\n' "$target" "$dev" >> "$TRACE"
printf '%s\\n' "$dev" >> "$target"
case $target in
    */drivers_probe)
        ov=$(cat "$SYSFS_PCI/devices/$dev/driver_override" 2>/dev/null)
        if [ -z "$ov" ]; then
            ov=$(cat "$STATE/default/$dev" 2>/dev/null)
        fi
        if [ -n "$ov" ] && [ -d "$SYSFS_PCI/drivers/$ov" ]; then
            ln -sfn "../../drivers/$ov" "$SYSFS_PCI/devices/$dev/driver"
        fi
        ;;
    */unbind)
        rm -f "$SYSFS_PCI/devices/$dev/driver"
        ;;
esac
exit 0
"""

STUB_SYSTEMCTL = """\
#!/bin/sh
printf 'systemctl %s\\n' "$*" >> "$TRACE"
exit 0
"""

STUB_FUSER = """\
#!/bin/sh
exit 1
"""


class FakeHost:
    """A sysfs tree, a set of stubs, and the hook run against both."""

    def __init__(self, root):
        self.root = root
        self.sysfs = root / "sys" / "bus" / "pci"
        self.state = root / "state"
        self.bin = root / "bin"
        self.trace = self.state / "trace"
        self.log = root / "vfio-hook.log"

        self.state.mkdir(parents=True)
        self.bin.mkdir(parents=True)
        self.trace.write_text("")
        (self.state / "lsmod.full").write_text(LSMOD_FULL)
        (self.state / "lsmod").write_text(LSMOD_FULL)

        for name, body in (
            ("modprobe", STUB_MODPROBE),
            ("lsmod", STUB_LSMOD),
            ("tee", STUB_TEE),
            ("systemctl", STUB_SYSTEMCTL),
            ("fuser", STUB_FUSER),
        ):
            stub = self.bin / name
            stub.write_text(body)
            stub.chmod(0o755)

        (self.sysfs / "drivers_probe").parent.mkdir(parents=True)
        (self.sysfs / "drivers_probe").write_text("")
        (self.state / "default").mkdir()
        for device, driver in ((DGPU, "nvidia"), (DGPU_AUDIO, "snd_hda_intel")):
            self.add_driver(driver)
            node = self.sysfs / "devices" / device
            node.mkdir(parents=True)
            (node / "driver_override").write_text("")
            (node / "driver").symlink_to(Path("../../drivers") / driver)
            # What drivers_probe binds when nothing overrides it -- the fake
            # kernel's stand-in for module matching.
            (self.state / "default" / device).write_text(driver)

        self.conf = root / "vfio.conf"
        self.conf.write_text(
            f'VFIO_DEVICES="{DGPU} {DGPU_AUDIO}"\nVFIO_LOG="{self.log}"\n'
        )

    def add_driver(self, name):
        directory = self.sysfs / "drivers" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "unbind").touch()

    def run(self, op="prepare", subop="begin", xml=CLAIMING_DOMAIN):
        return subprocess.run(
            ["bash", str(vfio.HOOK_ASSET), "win11", op, subop, "-"],
            input=xml,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "VFIO_CONF": str(self.conf),
                "SYSFS_PCI": str(self.sysfs),
                "TRACE": str(self.trace),
                "STATE": str(self.state),
            },
        )

    @property
    def steps(self):
        """The trace with tmp paths stripped, so assertions stay readable."""
        return [
            line.replace(f"{self.sysfs}/", "").rstrip()
            for line in self.trace.read_text().splitlines()
        ]

    def driver_of(self, device):
        link = self.sysfs / "devices" / device / "driver"
        return link.readlink().name if link.is_symlink() else "(none)"

    def override(self, device):
        return (self.sysfs / "devices" / device / "driver_override").read_text().strip()


@pytest.fixture
def fake_host(tmp_path):
    return FakeHost(tmp_path)


def test_hook_writes_the_override_before_unloading_nvidia(fake_host):
    """The fix for the wedge: the override closes the re-probe window first.

    Written after the unload instead, there is a gap in which udev re-probes
    the card and nvidia takes it back -- and it did, on 2026-08-04.
    """
    assert fake_host.run().returncode == 0

    # What the override said at the moment modprobe -r ran. Empty would mean
    # the window was open.
    unload = fake_host.steps.index("modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia")
    assert fake_host.steps[unload + 1] == "override-was vfio-pci"

    # And no write to the PCI driver core happened before the unload at all.
    assert not [step for step in fake_host.steps[:unload] if step.startswith("write ")]


def test_hook_hands_both_functions_to_vfio_pci(fake_host):
    out = fake_host.run()

    assert out.returncode == 0
    assert fake_host.driver_of(DGPU) == "vfio-pci"
    assert fake_host.driver_of(DGPU_AUDIO) == "vfio-pci"
    assert "handover complete" in fake_host.log.read_text()

    # The audio function is on the one driver the allow-list names, so it is
    # detached; the GPU function was already free once the module went.
    assert "write drivers/snd_hda_intel/unbind 0000:01:00.1" in fake_host.steps


def test_hook_refuses_to_unbind_a_card_nvidia_took_back(fake_host):
    """The guard, shown catching what it exists to catch.

    unload_nvidia can report success while the card is back on nvidia: udev
    re-probes it, or the kernel killed whatever held it. Unbinding then is the
    write that parks a task in state R and hangs libvirtd behind it.
    """
    (fake_host.state / "regrab").touch()

    out = fake_host.run()

    assert out.returncode == 1
    assert "refusing to unbind" in fake_host.log.read_text()
    assert not [step for step in fake_host.steps if "drivers/nvidia/unbind" in step]
    # Host left usable: no VM, but the card can still go back to nvidia.
    assert fake_host.override(DGPU) == ""
    assert fake_host.override(DGPU_AUDIO) == ""
    assert "modprobe nvidia_drm" in fake_host.steps


def test_hook_believes_modprobe_over_a_later_lsmod(fake_host):
    """`FATAL: Module nvidia_drm is in use.` is the answer, not the lsmod after.

    The old code logged that line and judged by an lsmod a second later. That
    lsmod came back clean -- because the kernel had killed the process holding
    the card, taking the user's graphical session with it -- so the hook called
    the unload a success and carried on. The modules being gone is not the same
    question as the unload having gone well.
    """
    (fake_host.state / "in-use").touch()

    out = fake_host.run()

    assert out.returncode == 1
    assert "cannot free the dGPU" in out.stderr
    assert "FAIL: modprobe -r exited 1" in fake_host.log.read_text()
    assert not [step for step in fake_host.steps if step.startswith("write ")]
    assert fake_host.override(DGPU) == ""


def test_hook_unloads_only_the_modules_that_are_loaded(fake_host):
    """Otherwise the exit code read above means nothing.

    modprobe -r exits non-zero for a module that is not loaded, so passing the
    full list would turn a partially loaded stack into a failed handover.
    """
    (fake_host.state / "lsmod").write_text("nvidia_modeset  1921024  0\nnvidia  18186240  1\n")

    assert fake_host.run().returncode == 0
    assert "modprobe -r nvidia_modeset nvidia" in fake_host.steps


def test_hook_clears_the_override_before_nvidia_comes_back(fake_host):
    """release/end is the reverse order, for the same reason."""
    assert fake_host.run().returncode == 0
    fake_host.trace.write_text("")

    assert fake_host.run(op="release", subop="end").returncode == 0

    # Cleared before the modules come back, so nothing is ever asked to detach
    # a card nvidia has already re-attached to.
    assert fake_host.override(DGPU) == ""
    reload_at = fake_host.steps.index("modprobe nvidia_drm")
    assert fake_host.steps[reload_at + 1] == "override-was"
    assert [step for step in fake_host.steps[:reload_at] if "vfio-pci/unbind" in step]

    assert fake_host.driver_of(DGPU) == "nvidia"
    assert fake_host.driver_of(DGPU_AUDIO) == "snd_hda_intel"
    assert "card returned to the host" in fake_host.log.read_text()


def test_hook_release_touches_nothing_after_a_refused_handover(fake_host):
    """libvirt calls release/end even for a guest prepare/begin turned away.

    The card never left nvidia in that case, so there is nothing to unbind and
    nothing to probe -- and drivers_probe is not a file to write on a hunch.
    """
    (fake_host.state / "regrab").touch()
    assert fake_host.run().returncode == 1
    fake_host.trace.write_text("")

    assert fake_host.run(op="release", subop="end").returncode == 0
    assert not [step for step in fake_host.steps if step.startswith("write ")]
    assert fake_host.driver_of(DGPU) == "nvidia"


def test_hook_leaves_a_guest_that_wants_no_gpu_alone(fake_host):
    out = fake_host.run(xml=DECOY_DOMAIN)

    assert out.returncode == 0
    assert fake_host.steps == []
    assert fake_host.driver_of(DGPU) == "nvidia"
    assert fake_host.override(DGPU) == ""


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


def test_iwd_set_option_replaces_existing_key():
    text = (
        "[General]\n"
        "EnableNetworkConfiguration=true\n"
        "\n"
        "[Network]\n"
        "NameResolvingService=systemd\n"
    )
    out = iwd.set_option(text, "General", "EnableNetworkConfiguration", "false")

    assert "EnableNetworkConfiguration = false" in out
    assert "true" not in out
    # dokunulmayan her şey yerinde kalmalı
    assert "NameResolvingService=systemd" in out


def test_iwd_set_option_inserts_under_existing_section():
    text = "# elle yazilmis\n[General]\nUseDefaultInterface=true\n"
    out = iwd.set_option(text, "General", "EnableNetworkConfiguration", "false")

    lines = out.splitlines()
    assert lines[0] == "# elle yazilmis"  # yorum korunur
    assert lines[1] == "[General]"
    assert lines[2] == "EnableNetworkConfiguration = false"
    assert "UseDefaultInterface=true" in out


def test_iwd_set_option_ignores_the_key_in_another_section():
    """iwd bu seçeneği [General] altından okur.

    Bölümden bağımsız arama, [Network] içindeki aynı isimli satırı
    değiştirir ve [General]'a hiç eklemez: ayar hiçbir şey yapmaz ama
    görev başarılı raporlar.
    """
    text = (
        "[Network]\n"
        "EnableNetworkConfiguration=true\n"
        "\n"
        "[General]\n"
        "AddressRandomization=once\n"
    )
    out = iwd.set_option(text, "General", "EnableNetworkConfiguration", "false")

    lines = out.splitlines()
    assert lines[1] == "EnableNetworkConfiguration=true"  # [Network] dokunulmaz
    assert lines[3] == "[General]"
    assert lines[4] == "EnableNetworkConfiguration = false"


def test_iwd_set_option_appends_missing_section():
    out = iwd.set_option("", "General", "EnableNetworkConfiguration", "false")
    assert out == "[General]\nEnableNetworkConfiguration = false\n"


@pytest.fixture
def iwd_conf(tmp_path, monkeypatch):
    conf = tmp_path / "main.conf"
    monkeypatch.setattr(iwd, "MAIN_CONF", conf)
    return conf


def test_iwd_netconfig_backs_up_and_rewrites(iwd_conf, monkeypatch, fake_write, runlog):
    iwd_conf.write_text("[General]\nEnableNetworkConfiguration=true\n", encoding="utf-8")
    monkeypatch.setattr(iwd.services, "is_active", lambda n: n == "systemd-networkd")
    monkeypatch.setattr(iwd, "sudo_write", fake_write)
    monkeypatch.setattr(iwd, "run", runlog)

    assert iwd.configure() == 0

    assert ["sudo", "cp", str(iwd_conf), f"{iwd_conf}.bak"] in runlog.calls
    assert "EnableNetworkConfiguration = false" in iwd_conf.read_text()


def test_iwd_netconfig_is_a_noop_when_already_disabled(iwd_conf, monkeypatch):
    iwd_conf.write_text(
        "[General]\nEnableNetworkConfiguration = false\n", encoding="utf-8"
    )
    written = []
    monkeypatch.setattr(iwd.services, "is_active", lambda n: True)
    monkeypatch.setattr(iwd, "sudo_write", lambda p, c: written.append(p) or 0)

    assert iwd.configure() == 0
    assert written == []


def test_iwd_netconfig_asks_when_no_manager_runs(iwd_conf, monkeypatch):
    iwd_conf.write_text("[General]\nEnableNetworkConfiguration=true\n", encoding="utf-8")
    written = []
    monkeypatch.setattr(iwd.services, "is_active", lambda n: False)
    monkeypatch.setattr(iwd.prompt, "ask_yes", lambda q: False)
    monkeypatch.setattr(iwd, "sudo_write", lambda p, c: written.append(p) or 0)

    assert iwd.configure() == 0
    assert written == []
    assert "true" in iwd_conf.read_text()  # dosyaya dokunulmadi


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
    disables = []

    monkeypatch.setattr(virt, "run", runlog)
    monkeypatch.setattr(virt, "sudo_write", fake_write)
    monkeypatch.setattr(virt.pacman, "is_installed", lambda p: True)
    monkeypatch.setattr(virt.services, "disable", lambda n: disables.append(n) or 0)
    monkeypatch.setattr(virt, "_group_exists", lambda g: g == "kvm")
    monkeypatch.setattr(virt.getpass, "getuser", lambda: "drpars")
    monkeypatch.setattr(virt, "LIBVIRTD_CONF", etc / "libvirtd.conf")
    monkeypatch.setattr(virt, "QEMU_CONF", etc / "qemu.conf")
    monkeypatch.setattr(virt, "NETWORK_CONF", etc / "network.conf")
    monkeypatch.setattr(gpuconfig, "sudo_write", fake_write)
    monkeypatch.setattr(gpuconfig, "MKINITCPIO", mk)

    assert virt.configure() == 0
    assert "unix_sock_group = 'libvirt'" in (etc / "libvirtd.conf").read_text()

    # virtio_* are guest drivers: the host must not pull them into the initramfs
    # (linux-g14 does not even build them).
    assert mk.read_text() == "MODULES=()\n"
    assert ["sudo", "mkinitcpio", "-P"] not in runlog.calls

    # Socket activation, not a boot-time service.
    sockets = ["sudo", "systemctl", "enable", "--now", *virt.LIBVIRT_SOCKETS]
    assert sockets in runlog.calls
    assert ["sudo", "systemctl", "start", "libvirtd.service"] not in runlog.calls
    assert disables == ["libvirtd.service"]
    # virsh talks to the socket, so it has to be up first.
    assert runlog.calls.index(sockets) < runlog.calls.index(
        ["sudo", "virsh", "net-autostart", "default"]
    )

    # idempotent
    runlog.calls.clear()
    assert virt.configure() == 0
    assert "unix_sock_group = 'libvirt'" in (etc / "libvirtd.conf").read_text()


def test_libvirt_sockets_enabled_after_service_disable(monkeypatch, tmp_path,
                                                       fake_write, runlog):
    """Order matters: libvirtd.service's Also= lines take the sockets with it.

    Enabling the sockets first and disabling the service afterwards would undo
    the enable and leave the machine with no way to reach libvirtd at all.
    """
    order = []
    monkeypatch.setattr(virt, "run", lambda cmd, **kw: order.append(cmd) or 0)
    monkeypatch.setattr(
        virt.services, "disable", lambda n: order.append(["disable", n]) or 0
    )

    assert virt._enable_libvirt_sockets() == 0
    assert order[0] == ["disable", "libvirtd.service"]
    assert order[1] == ["sudo", "systemctl", "enable", "--now", *virt.LIBVIRT_SOCKETS]


def test_waydroid_builtin_vs_dkms(tmp_path, monkeypatch, fake_write):
    """Built-in binder must skip the DKMS module, whatever the kernel is called.

    Installing binder_linux-dkms next to a built-in binder fails with EBUSY and
    drags systemd-modules-load.service down with it.
    """
    procfs = tmp_path / "filesystems"
    procfs.write_text("nodev\tsysfs\nnodev\tbinder\n\text4\n")
    enables, installs = [], []
    monkeypatch.setattr(waydroid, "sudo_write", fake_write)
    monkeypatch.setattr(waydroid, "FILESYSTEMS", procfs)
    monkeypatch.setattr(waydroid.pacman, "is_installed", lambda p: p == "waydroid")
    monkeypatch.setattr(waydroid.pacman, "install", lambda r, a: installs.append(tuple(a)) or 0)
    monkeypatch.setattr(waydroid.services, "enable", lambda n: enables.append(n) or 0)
    monkeypatch.setattr(waydroid, "MODULES_LOAD", tmp_path / "ml.conf")
    monkeypatch.setattr(waydroid, "MODPROBE", tmp_path / "mp.conf")

    assert waydroid.setup() == 0
    assert installs == []
    assert not (tmp_path / "mp.conf").exists()
    assert enables == ["waydroid-container"]

    procfs.write_text("nodev\tsysfs\n\text4\n")
    assert waydroid.setup() == 0
    assert installs == [("binder_linux-dkms", "python-pyclip")]
    assert (
        (tmp_path / "mp.conf").read_text()
        == "options binder_linux devices=binder,hwbinder,vndbinder\n"
    )


def test_binderfs_is_not_binder(tmp_path, monkeypatch):
    """binderfs is the control filesystem, not the driver a substring match
    would happily confuse it with."""
    procfs = tmp_path / "filesystems"
    monkeypatch.setattr(waydroid, "FILESYSTEMS", procfs)

    procfs.write_text("nodev\tbinderfs\n")
    assert waydroid.has_builtin_binder() is False

    procfs.write_text("nodev\tbinder\n")
    assert waydroid.has_builtin_binder() is True


def test_binder_check_survives_missing_procfs(tmp_path, monkeypatch):
    monkeypatch.setattr(waydroid, "FILESYSTEMS", tmp_path / "nope")
    assert waydroid.has_builtin_binder() is False


# --- XDG user directories ---


def test_xdg_dir_creates_the_folders_when_they_are_missing(tmp_path, monkeypatch, runlog):
    """xdg-user-dir yapilandirilmamis klasor icin $HOME cevaplar.

    Bu cevabi oldugu gibi kabul etmek duvar kagitlarini ~/Pictures/Wallpaper
    yerine ~/Wallpaper icine koyuyordu -- hata yok, yanlis yer.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(dotfiles.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(dotfiles, "run", runlog)
    monkeypatch.setattr(dotfiles.shutil, "which", lambda name: "/usr/bin/" + name)

    answers = iter([home, home / "Pictures"])  # once $HOME, guncelleme sonrasi dogru
    monkeypatch.setattr(dotfiles, "_query_xdg", lambda name: next(answers))

    assert dotfiles._xdg_dir("PICTURES", "Pictures") == home / "Pictures"
    assert ["xdg-user-dirs-update"] in runlog.calls
    assert (home / "Pictures").is_dir()


def test_xdg_dir_falls_back_to_the_english_name(tmp_path, monkeypatch, runlog):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(dotfiles.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(dotfiles, "run", runlog)
    monkeypatch.setattr(dotfiles, "_create_xdg_dirs", lambda: False)
    monkeypatch.setattr(dotfiles, "_query_xdg", lambda name: None)

    assert dotfiles._xdg_dir("PICTURES", "Pictures") == home / "Pictures"
    assert (home / "Pictures").is_dir()


# --- reflector ---


def test_reflector_backs_up_the_working_mirrorlist(tmp_path, monkeypatch, runlog):
    mirrorlist = tmp_path / "mirrorlist"
    mirrorlist.write_text("Server = https://example.invalid/$repo/os/$arch\n")
    monkeypatch.setattr(tasks, "MIRRORLIST", mirrorlist)
    monkeypatch.setattr(tasks, "run", runlog)
    monkeypatch.setattr(tasks.shutil, "which", lambda name: "/usr/bin/reflector")
    monkeypatch.setattr(tasks, "input", lambda prompt="": "Turkey", raising=False)

    assert tasks.reflector_mirrors() == 0
    # Kotu bir yansi listesi her pacman cagrisini durdurur, reflector'u
    # yeniden kuracak olani dahil; calisan kopya saklanmali.
    assert ["sudo", "cp", str(mirrorlist), f"{mirrorlist}.bak"] in runlog.calls
    save = next(c for c in runlog.calls if c[:2] == ["sudo", "reflector"])
    assert save[-2:] == ["--save", str(mirrorlist)]
    assert "--country" in save and "Turkey" in save


def test_kmscon_layout_choice_drops_a_foreign_variant(tmp_path, monkeypatch, fake_write, runlog):
    """Duzen degistirilirse varyant tasinmamali.

    Varyant yazildigi duzene aittir; "tr" icin gecerli bir varyanti "us"
    ile birlestirmek keymap derlemesini dusurur ve kmscon sessizce
    varsayilan sistem klavyesine doner.
    """
    vconsole = tmp_path / "vconsole.conf"
    vconsole.write_text("XKBLAYOUT=tr\nXKBMODEL=pc105\nXKBVARIANT=f\n")
    conf = tmp_path / "kmscon" / "kmscon.conf"
    monkeypatch.setattr(kmscon, "VCONSOLE", vconsole)
    monkeypatch.setattr(kmscon, "CONFIG", conf)
    monkeypatch.setattr(kmscon, "sudo_write", fake_write)
    monkeypatch.setattr(kmscon, "run", runlog)
    monkeypatch.setattr(kmscon, "_ask_tty", lambda: 5)
    monkeypatch.setattr(kmscon, "_ask_size", lambda: 16)
    monkeypatch.setattr(kmscon, "_font", lambda: kmscon.FONT)
    monkeypatch.setattr(kmscon, "_ask_layout", lambda default: "us")
    monkeypatch.setattr(kmscon.pacman, "install", lambda repo, aur: 0)
    monkeypatch.setattr(kmscon.services, "disable", lambda n: 0)
    monkeypatch.setattr(kmscon.services, "enable", lambda n: 0)

    assert kmscon.install() == 0
    text = conf.read_text()
    assert "xkb-layout=us" in text
    assert "xkb-variant" not in text


def test_aur_install_bootstraps_a_helper(monkeypatch, runlog):
    """AUR paketi isteyen ilk gorev, yardimci yokken cuvallamamali.

    yay'in kendisi de AUR'da oldugu icin "once Sistem Guncelleme'den
    kurun" demek sorunu bir adim oteye tasiyordu.
    """
    from archsetup.core import pacman

    helpers = iter([None, "yay"])  # once yok, yay-bin kurulduktan sonra var
    monkeypatch.setattr(pacman, "detect_aur_helper", lambda: next(helpers))
    monkeypatch.setattr(pacman, "run", runlog)
    monkeypatch.setattr(pacman, "install_from_aur_git", lambda url: runlog(["makepkg", url]))
    # ensure_aur_helper() ask_yes'i cagri aninda ice aktariyor, modulu yamalamak yeter.
    import archsetup.core.prompt as prompt_mod
    monkeypatch.setattr(prompt_mod, "ask_yes", lambda q: True)

    assert pacman.install([], ["kmscon-git"]) == 0
    assert ["makepkg", pacman.YAY_BIN] in runlog.calls
    assert ["yay", "-S", "--needed", "kmscon-git"] in runlog.calls


def test_aur_install_fails_cleanly_when_declined(monkeypatch, runlog):
    from archsetup.core import pacman
    import archsetup.core.prompt as prompt_mod

    monkeypatch.setattr(pacman, "detect_aur_helper", lambda: None)
    monkeypatch.setattr(pacman, "run", runlog)
    monkeypatch.setattr(prompt_mod, "ask_yes", lambda q: False)

    assert pacman.install([], ["kmscon-git"]) != 0
    assert runlog.calls == []


# --- coding agents ---


@pytest.fixture
def agent_env(tmp_path, monkeypatch, runlog):
    """Kurucu betigi indirilmis gibi davranan sahte curl + sh."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr(coding_agents, "LOCAL_BIN", bindir)
    monkeypatch.setattr(coding_agents.shutil, "which",
                        lambda name: "/usr/bin/curl" if name == "curl" else None)

    def fake_run(cmd, **kwargs):
        runlog(cmd)
        if cmd[0] == "curl":
            Path(cmd[-1]).write_text("#!/bin/sh\ntrue\n")
        else:  # kurucu betik calisti; ikiliyi birakmis olmali
            (bindir / "claude").write_text("")
            (bindir / "codewhale").write_text("")
        return 0

    monkeypatch.setattr(coding_agents, "run", fake_run)
    monkeypatch.setenv("PATH", str(bindir))
    return runlog


def test_installer_is_downloaded_then_run_not_piped(agent_env):
    """curl | bash yerine once dosyaya, sonra calistir.

    Boruya baglanan kabuk baytlari geldikce calistirir; yarida kopan bir
    baglanti yarim kurucuyu calistirir. Once dosyaya yazmak bunu "indirme
    basarisiz, hicbir sey calismadi" haline getirir.
    """
    assert coding_agents.install_claude_code() == 0

    curl, shell = agent_env.calls
    assert curl[:3] == ["curl", "-fsSL", "https://claude.ai/install.sh"]
    assert curl[3] == "-o"  # boruya degil, dosyaya
    script = curl[4]
    assert shell == ["bash", script]  # her proje kendi belgeledigi yorumlayici


def test_codewhale_uses_its_own_documented_shell(agent_env):
    assert coding_agents.install_codewhale() == 0
    curl, shell = agent_env.calls
    assert curl[2] == "https://codewhale.net/install.sh"
    assert shell[0] == "sh"


def test_empty_download_does_not_reach_the_shell(tmp_path, monkeypatch, runlog):
    """curl -f cogu hatayi yakalar ama bos govdeli 200 de bir basaridir."""
    monkeypatch.setattr(coding_agents, "LOCAL_BIN", tmp_path / "bin")
    monkeypatch.setattr(coding_agents.shutil, "which",
                        lambda name: "/usr/bin/curl" if name == "curl" else None)

    def fake_run(cmd, **kwargs):
        runlog(cmd)
        if cmd[0] == "curl":
            Path(cmd[-1]).write_text("")  # bos
        return 0

    monkeypatch.setattr(coding_agents, "run", fake_run)
    assert coding_agents.install_claude_code() != 0
    assert [c[0] for c in runlog.calls] == ["curl"]  # kabuk hic cagrilmadi


def test_an_older_copy_earlier_on_path_is_reported(tmp_path, monkeypatch, capsys, runlog):
    """npm -g ile kurulmus eski kopya PATH'te ondeyse yeni surum golgede kalir."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    npm_copy = tmp_path / "npm-global" / "bin" / "claude"
    npm_copy.parent.mkdir(parents=True)
    npm_copy.write_text("")

    monkeypatch.setattr(coding_agents, "LOCAL_BIN", bindir)
    monkeypatch.setattr(
        coding_agents.shutil, "which",
        lambda name: {"curl": "/usr/bin/curl", "claude": str(npm_copy)}.get(name),
    )
    monkeypatch.setattr(coding_agents, "ask_yes", lambda q: True)

    def fake_run(cmd, **kwargs):
        runlog(cmd)
        if cmd[0] == "curl":
            Path(cmd[-1]).write_text("#!/bin/sh\ntrue\n")
        else:
            (bindir / "claude").write_text("")
        return 0

    monkeypatch.setattr(coding_agents, "run", fake_run)
    monkeypatch.setenv("PATH", str(bindir))

    assert coding_agents.install_claude_code() == 0
    assert str(npm_copy) in capsys.readouterr().out


def test_sleep_services_task_leaves_out_the_laptop_only_bits(monkeypatch, tmp_path):
    """Masaustu de bu gorevi calistirabilmeli.

    Dizustu ayarlari (S0ix, DynamicPowerManagement) ve Dynamic Boost
    (nvidia-powerd) buraya girmemeli; yoksa askiya alinan bir masaustunde
    VRAM korumasi ancak anlamsiz ayarlari kabul ederek acilabilirdi.
    """
    enabled = []
    monkeypatch.setattr(nvidia_laptop.hardware, "gpu_matches", lambda name: True)
    monkeypatch.setattr(nvidia_laptop.pacman, "is_installed", lambda name: True)
    monkeypatch.setattr(nvidia_laptop.services, "unit_exists", lambda unit: True)
    monkeypatch.setattr(
        nvidia_laptop.services, "enable", lambda unit: enabled.append(unit) or 0
    )
    monkeypatch.setattr(nvidia_laptop, "MODPROBE_CONF", tmp_path / "nvidia.conf")
    monkeypatch.setattr(nvidia_laptop, "ask_yes", lambda prompt: False)

    assert nvidia_laptop.enable_sleep_services() == 0

    assert enabled == list(nvidia_laptop.SLEEP_SERVICES)
    assert nvidia_laptop.POWERD_SERVICE not in enabled
    assert not (tmp_path / "nvidia.conf").exists()


def test_sleep_services_add_suspend_then_hibernate_when_asked(monkeypatch):
    enabled = []
    monkeypatch.setattr(nvidia_laptop.hardware, "gpu_matches", lambda name: True)
    monkeypatch.setattr(nvidia_laptop.pacman, "is_installed", lambda name: True)
    monkeypatch.setattr(nvidia_laptop.services, "unit_exists", lambda unit: True)
    monkeypatch.setattr(
        nvidia_laptop.services, "enable", lambda unit: enabled.append(unit) or 0
    )
    monkeypatch.setattr(nvidia_laptop, "ask_yes", lambda prompt: True)

    assert nvidia_laptop.enable_sleep_services() == 0
    assert nvidia_laptop.S2H_SERVICE in enabled


def test_laptop_task_warns_on_a_desktop_and_can_be_declined(monkeypatch, tmp_path, capsys):
    """Onceden gorevi masaustunde engelleyen tek sey menu basligiydi."""
    monkeypatch.setattr(nvidia_laptop.hardware, "gpu_matches", lambda q: True)
    monkeypatch.setattr(nvidia_laptop.hardware, "is_laptop", lambda: False)
    monkeypatch.setattr(nvidia_laptop.hardware, "chassis", lambda: "desktop")
    monkeypatch.setattr(nvidia_laptop.pacman, "is_installed", lambda p: True)
    monkeypatch.setattr(nvidia_laptop, "MODPROBE_CONF", tmp_path / "nvidia.conf")
    monkeypatch.setattr(nvidia_laptop, "ask_yes", lambda prompt: False)

    assert nvidia_laptop.configure() == 0
    assert not (tmp_path / "nvidia.conf").exists()
    # Dogru gorev soylenmeli, yoksa kullanici uyariyi asip bunu calistirir.
    assert "nvidia-sleep" in capsys.readouterr().out


def test_unknown_chassis_is_not_treated_as_a_desktop(monkeypatch, tmp_path, capsys):
    """Canli ISO'da hostnamectl cevap vermeyebilir; sessizce reddetme."""
    monkeypatch.setattr(nvidia_laptop.hardware, "gpu_matches", lambda q: True)
    monkeypatch.setattr(nvidia_laptop.hardware, "is_laptop", lambda: None)
    monkeypatch.setattr(nvidia_laptop.hardware, "chassis", lambda: "")
    monkeypatch.setattr(nvidia_laptop.pacman, "is_installed", lambda p: True)
    monkeypatch.setattr(nvidia_laptop, "ask_yes", lambda prompt: False)

    assert nvidia_laptop.configure() == 0
    out = capsys.readouterr().out
    assert "desktop" not in out.lower()


def test_chassis_falls_back_to_dmi_when_hostnamectl_is_absent(monkeypatch, tmp_path):
    dmi = tmp_path / "chassis_type"
    dmi.write_text("10\n")  # Notebook
    monkeypatch.setattr(hardware, "CHASSIS_TYPE", dmi)
    monkeypatch.setattr(
        hardware.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    hardware.chassis.cache_clear()

    assert hardware.chassis() == "laptop"
    assert hardware.is_laptop() is True
    hardware.chassis.cache_clear()


def test_password_is_asked_before_the_services_are_started(tmp_path, monkeypatch, samba_env):
    """Baslatmak 2 dakika surebiliyor; kullanici o beklemeyi sebepsiz gormemeli.

    smb.service `Wants=/After=network-online.target` tasidigi icin onu
    baslatmak systemd-networkd-wait-online'i de cekiyor. Drop-in yokken o birim
    tum arayuzlerin routable olmasini bekliyor ve bos ethernet portu varken
    120 saniyede timeout'a dusuyor. smbpasswd calisan bir smbd istemedigi icin
    once sorulabilir.
    """
    monkeypatch.setattr(network.prompt, "ask_yes", lambda q: True)

    assert network.configure() == 0
    calls = samba_env.runlog.calls
    assert calls.index(["sudo", "smbpasswd", "-a", "drpars"]) < calls.index(
        ["sudo", "systemctl", "restart", "smb.service", "nmb.service"]
    )


def test_missing_wait_online_dropin_is_offered_before_starting(tmp_path, monkeypatch, samba_env):
    """Ayni bekleme her onyuklemede yasaniyor; burada duzeltmek daha degerli."""
    monkeypatch.setattr(network.prompt, "ask_yes", lambda q: True)
    monkeypatch.setattr(network.services, "unit_exists", lambda n: True)
    applied = []
    monkeypatch.setattr(
        network, "wait_online_timeout", lambda: applied.append(True) or 0
    )

    assert network.configure() == 0
    assert applied == [True]


def test_existing_wait_online_dropin_is_not_offered_again(tmp_path, monkeypatch, samba_env):
    (tmp_path / "any.conf").write_text("[Service]\n")
    monkeypatch.setattr(network.prompt, "ask_yes", lambda q: True)
    monkeypatch.setattr(network.services, "unit_exists", lambda n: True)
    monkeypatch.setattr(
        network, "wait_online_timeout", lambda: pytest.fail("tekrar uygulanmamaliydi")
    )

    assert network.configure() == 0
