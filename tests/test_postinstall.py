"""Post-install tasks: dotfiles, sddm, kmscon, network, asus, virt, waydroid."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from archsetup.core import (
    asus,
    audio_dsp,
    coding_agents,
    coredump,
    dotfiles,
    iwd,
    hardware,
    gpuconfig,
    kmscon,
    network,
    nvidia_laptop,
    sddm,
    tasks,
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
    assert installed == [([], ["kmscon-git"])]


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
