"""Tests for core.kms_hook.

Every branch here reads something real on a live machine -- module trees,
sysfs uevents, modprobe's merged configuration, image sizes on a root-owned
ESP -- so all four roots are module constants and every one of them is
pointed at tmp_path. Nothing in this file may give a different answer on the
laptop, on the desktop or in the CI container.
"""

import json
import subprocess
from pathlib import Path

import pytest

from archsetup.core import hardware, kms_hook, mkinitcpio, pacman, secureboot, sysedit


# --- mkinitcpio text helpers -------------------------------------------------


def test_read_array_distinguishes_empty_from_missing():
    assert mkinitcpio.read_array("MODULES=()\n", "MODULES") == []
    assert mkinitcpio.read_array("HOOKS=(base udev)\n", "HOOKS") == ["base", "udev"]
    assert mkinitcpio.read_array("MODULES=()\n", "HOOKS") is None


def test_set_array_keeps_the_rest_of_the_file():
    text = "# note\nMODULES=(a b)\nHOOKS=(base kms fsck)\n"
    out = mkinitcpio.set_array(text, "HOOKS", ["base", "fsck"])
    assert out == "# note\nMODULES=(a b)\nHOOKS=(base fsck)\n"
    assert mkinitcpio.set_array(text, "NOPE", ["x"]) is None


def test_effective_text_appends_drop_ins(tmp_path):
    conf = tmp_path / "mkinitcpio.conf"
    conf.write_text("HOOKS=(base kms fsck)\n")
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()
    (conf_d / "10-zz.conf").write_text("HOOKS=(base fsck)\n")

    text = mkinitcpio.effective_text(conf, conf_d)
    assert text.count("HOOKS=") == 2
    # Later definitions win, so the value in force is the drop-in's, not the
    # main file's -- reading the first match would report kms as still there.
    assert mkinitcpio.read_array(text, "HOOKS") == ["base", "fsck"]


def test_preset_outputs_skips_commented_assignments():
    text = (
        "PRESETS=('default' 'fallback')\n"
        '#default_image="/boot/initramfs-linux.img"\n'
        'default_uki="/efi/EFI/Linux/arch.efi"\n'
        'fallback_image="/boot/initramfs-linux-fallback.img"\n'
    )
    assert mkinitcpio.preset_outputs(text) == [
        Path("/efi/EFI/Linux/arch.efi"),
        Path("/boot/initramfs-linux-fallback.img"),
    ]


# --- kms_hook measurement ----------------------------------------------------


@pytest.fixture
def kms_env(tmp_path, monkeypatch, runlog):
    """A whole fake machine: two kernels, a sysfs tree and a fake modprobe."""
    conf = tmp_path / "mkinitcpio.conf"
    conf.write_text(
        "MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)\n"
        "HOOKS=(base systemd autodetect modconf kms keyboard block filesystems fsck)\n"
    )
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()

    modules_root = tmp_path / "modules"
    for kver, drivers in (("7.1-zen", ("nouveau", "amdgpu")), ("7.1-g14", ("amdgpu",))):
        drm = modules_root / kver / "kernel/drivers/gpu/drm"
        for driver in drivers:
            (drm / driver).mkdir(parents=True, exist_ok=True)
            (drm / driver / f"{driver}.ko.zst").write_bytes(b"")
        (modules_root / kver / "modules.builtin").write_text(
            "kernel/drivers/gpu/drm/sysfb/simpledrm.ko\n"
        )

    sys_devices = tmp_path / "sys"
    (sys_devices / "pci0000:00/0000:02:00.0").mkdir(parents=True)
    (sys_devices / "pci0000:00/0000:02:00.0/uevent").write_text(
        "DRIVER=nvidia\nMODALIAS=pci:v000010DEd00002484bc03sc00i00\n"
    )

    firmware = tmp_path / "firmware"
    (firmware / "nvidia/ga102").mkdir(parents=True)
    (firmware / "nvidia/ga102/gsp.bin").write_bytes(b"x" * 2048)

    monkeypatch.setattr(kms_hook, "CONF", conf)
    monkeypatch.setattr(kms_hook, "CONF_D", conf_d)
    monkeypatch.setattr(kms_hook, "MODULES_ROOT", modules_root)
    monkeypatch.setattr(kms_hook, "FIRMWARE_ROOT", firmware)
    monkeypatch.setattr(kms_hook, "SYS_DEVICES", sys_devices)
    monkeypatch.setattr(kms_hook, "BACKUP_DIR", tmp_path / "backup")
    monkeypatch.setattr(kms_hook, "ROOT", tmp_path / "root")
    monkeypatch.setattr(kms_hook, "run", runlog)
    # Secure Boot is read from efivarfs, so the machine running the tests would
    # otherwise decide whether the verification branch runs -- and on the box
    # this was written on it is on, which would put `sudo sbctl` in a test run.
    monkeypatch.setattr(secureboot, "SECURE_BOOT_VAR", tmp_path / "no-efivars")
    monkeypatch.setattr(hardware, "gpu_matches", lambda q: q == "nvidia")
    monkeypatch.setattr(pacman, "is_installed", lambda pkg: pkg == "nvidia-utils")

    # The one place a real machine is consulted: modprobe. The modalias of an
    # NVIDIA card resolves to nouveau as well as nvidia, which is the whole
    # reason the hook pulls nouveau in.
    def fake_capture(cmd):
        if cmd[0] == "modprobe" and "-qaR" in cmd:
            return "nvidia\nnvidia_drm\nnouveau\namdgpu\n"
        if cmd[0] == "modprobe" and cmd[1] == "-c":
            return "blacklist nouveau\nblacklist nova_core\n"
        if cmd[0] == "modinfo":
            return "nvidia/ga102/gsp.bin\n"
        return ""

    monkeypatch.setattr(kms_hook, "_capture", fake_capture)
    return tmp_path


def test_contribution_is_computed_not_guessed(kms_env):
    # The pool is what is on disk; the cache is what modprobe resolved.
    assert kms_hook.kms_contribution("7.1-zen") == {"nouveau", "amdgpu"}
    assert kms_hook.kms_contribution("7.1-g14") == {"amdgpu"}
    assert kms_hook.kernels() == ["7.1-g14", "7.1-zen"]
    assert kms_hook.blacklisted() == {"nouveau", "nova_core"}


def test_refuses_when_a_driver_would_be_lost(kms_env, capsys):
    """amdgpu is carried only by kms here, so removing it would drop it."""
    assert kms_hook.configure() == 1
    out = capsys.readouterr().out
    assert "amdgpu" in out
    assert "amd-modules" in out
    assert "kms" in kms_env.joinpath("mkinitcpio.conf").read_text()


def test_removes_the_hook_once_the_other_driver_is_whitelisted(
    kms_env, monkeypatch, capsys
):
    conf = kms_env / "mkinitcpio.conf"
    conf.write_text(conf.read_text().replace("nvidia_drm)", "nvidia_drm amdgpu)"))
    monkeypatch.setattr(kms_hook, "ask_yes", lambda q: True)

    preset_dir = kms_env / "root/etc/mkinitcpio.d"
    preset_dir.mkdir(parents=True)
    image = kms_env / "arch.efi"
    image.write_bytes(b"x" * 4096)
    (preset_dir / "linux.preset").write_text(
        f"PRESETS=('default')\ndefault_uki=\"{image}\"\n"
    )

    written = {}

    def fake_backup(path, content, backup=None):
        written["backup"] = backup
        Path(path).write_text(content, encoding="utf-8")
        return 0, True

    monkeypatch.setattr(sysedit, "write_with_backup", fake_backup)
    monkeypatch.setattr(kms_hook.sysedit, "write_with_backup", fake_backup)

    assert kms_hook.configure() == 0
    hooks = mkinitcpio.read_array(conf.read_text(), "HOOKS")
    assert "kms" not in hooks
    assert hooks[0] == "base" and hooks[-1] == "fsck"
    assert ["sudo", "mkinitcpio", "-P"] in kms_hook.run.calls
    # The sibling .bak is already taken on at least one machine here.
    assert written["backup"].name.startswith("mkinitcpio.conf.yedek-")
    assert "nouveau" in capsys.readouterr().out


def test_noop_when_the_hook_is_already_gone(kms_env, capsys):
    conf = kms_env / "mkinitcpio.conf"
    conf.write_text(conf.read_text().replace(" kms", ""))
    assert kms_hook.configure() == 0
    assert "kms" in capsys.readouterr().out


def test_drop_in_overrides_the_main_file(kms_env, capsys):
    """A drop-in that removes kms wins, so there is nothing left to do."""
    (kms_env / "conf.d/99-local.conf").write_text("HOOKS=(base systemd fsck)\n")
    assert kms_hook.configure() == 0
    assert "kms" in (kms_env / "mkinitcpio.conf").read_text()


def test_refuses_without_nvidia_or_driver(kms_env, monkeypatch):
    monkeypatch.setattr(hardware, "gpu_matches", lambda q: False)
    assert kms_hook.configure() == 1
    monkeypatch.setattr(hardware, "gpu_matches", lambda q: q == "nvidia")
    monkeypatch.setattr(pacman, "is_installed", lambda pkg: False)
    assert kms_hook.configure() == 1


def test_refuses_when_no_console_would_survive(kms_env, monkeypatch, capsys):
    """No simpledrm builtin and no DRM driver in MODULES means a blind boot."""
    conf = kms_env / "mkinitcpio.conf"
    conf.write_text(conf.read_text().replace("nvidia_drm)", "nvidia_drm amdgpu)"))
    for kver in ("7.1-zen", "7.1-g14"):
        (kms_env / "modules" / kver / "modules.builtin").write_text("")
    monkeypatch.setattr(kms_hook, "kms_pool", lambda kver: {"nouveau"})
    assert kms_hook.configure() == 1
    assert "7.1-zen" in capsys.readouterr().out


def test_nothing_to_drop_when_nothing_is_blacklisted(kms_env, monkeypatch, capsys):
    conf = kms_env / "mkinitcpio.conf"
    conf.write_text(conf.read_text().replace("nvidia_drm)", "nvidia_drm amdgpu)"))
    monkeypatch.setattr(kms_hook, "blacklisted", set)
    # nouveau is now neither blacklisted nor whitelisted, so it reads as a loss.
    monkeypatch.setattr(kms_hook, "kms_contribution", lambda kver: {"amdgpu"})
    assert kms_hook.configure() == 0
    assert "kms" in (kms_env / "mkinitcpio.conf").read_text()


def test_firmware_bytes_counts_compressed_variants(kms_env):
    firmware = kms_env / "firmware/nvidia/ga102"
    (firmware / "gsp.bin.zst").write_bytes(b"y" * 512)
    (firmware / "gsp.bin").unlink()
    assert kms_hook.firmware_bytes("7.1-zen", "nouveau") == 512


def test_firmware_bytes_counts_each_blob_once(kms_env, monkeypatch):
    """519 declared paths resolving to 222 inodes reported 1302 MiB for 103."""
    firmware = kms_env / "firmware/nvidia/ga102"
    for name in ("a.bin", "b.bin", "c.bin"):
        (firmware / name).symlink_to(firmware / "gsp.bin")
    declared = "nvidia/ga102/gsp.bin nvidia/ga102/a.bin nvidia/ga102/b.bin nvidia/ga102/c.bin"
    monkeypatch.setattr(
        kms_hook, "_capture",
        lambda cmd: declared if cmd[0] == "modinfo" else "",
    )
    # Four paths, one blob of 2048 bytes -- following symlinks would say 8192.
    assert kms_hook.firmware_bytes("7.1-zen", "nouveau") == 2048

    # And the set is shared, so a second kernel does not pay for it again.
    counted = set()
    assert kms_hook.firmware_bytes("7.1-zen", "nouveau", counted) == 2048
    assert kms_hook.firmware_bytes("7.1-g14", "nouveau", counted) == 0


# --- secure boot verification ------------------------------------------------


def _efivar(tmp_path, value: int, name: str = "SecureBoot") -> Path:
    """An efivarfs variable: four attribute bytes, then the value."""
    path = tmp_path / name
    path.write_bytes(b"\x06\x00\x00\x00" + bytes([value]))
    return path


def _sbctl(payload: str, returncode: int = 0):
    """A fake `sudo sbctl verify --json` run."""

    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["sudo", "sbctl"] and "--json" in cmd
        return subprocess.CompletedProcess(cmd, returncode, payload, "")

    return fake_run


@pytest.fixture
def sb_on(tmp_path, monkeypatch):
    monkeypatch.setattr(secureboot, "SECURE_BOOT_VAR", _efivar(tmp_path, 1))
    monkeypatch.setattr(secureboot.shutil, "which", lambda name: f"/usr/bin/{name}")
    return tmp_path


def test_enabled_needs_the_value_past_the_attribute_header(tmp_path, monkeypatch):
    monkeypatch.setattr(secureboot, "SECURE_BOOT_VAR", _efivar(tmp_path, 1, "on"))
    assert secureboot.enabled() is True
    monkeypatch.setattr(secureboot, "SECURE_BOOT_VAR", _efivar(tmp_path, 0, "off"))
    assert secureboot.enabled() is False
    # None is not False. A machine without efivarfs has not answered the
    # question, and the caller has to stay quiet for that reason rather than
    # act as though Secure Boot were known to be off.
    monkeypatch.setattr(secureboot, "SECURE_BOOT_VAR", tmp_path / "absent")
    assert secureboot.enabled() is None
    header_only = tmp_path / "short"
    header_only.write_bytes(b"\x06\x00\x00\x00")
    monkeypatch.setattr(secureboot, "SECURE_BOOT_VAR", header_only)
    assert secureboot.enabled() is None


def test_sbctl_is_not_even_asked_with_secure_boot_off(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(secureboot, "SECURE_BOOT_VAR", _efivar(tmp_path, 0))

    def explode():
        raise AssertionError("sbctl asked while Secure Boot is off")

    monkeypatch.setattr(secureboot, "report", explode)
    assert secureboot.verify([Path("/efi/EFI/Linux/arch.efi")]) == 0
    assert capsys.readouterr().out == ""


def test_unsigned_fails_even_though_sbctl_exits_zero(sb_on, monkeypatch, capsys):
    """Measured on sbctl 0.18: it prints `✗ ... is not signed` and exits 0.

    So the exit code cannot be the gate; the JSON is the only place the two
    answers differ.
    """
    payload = json.dumps(
        [
            {"file_name": "/efi/EFI/Linux/arch-linux-zen.efi", "is_signed": 0},
            {"file_name": "/efi/EFI/Linux/arch-linux-g14.efi", "is_signed": 1},
        ]
    )
    monkeypatch.setattr(secureboot.subprocess, "run", _sbctl(payload, returncode=0))
    rc = secureboot.verify(
        [
            Path("/efi/EFI/Linux/arch-linux-zen.efi"),
            Path("/efi/EFI/Linux/arch-linux-g14.efi"),
        ]
    )
    assert rc == 1
    out = capsys.readouterr().out
    assert "arch-linux-zen.efi" in out
    assert "arch-linux-g14.efi" not in out


def test_files_this_task_did_not_write_cannot_fail_it(sb_on, monkeypatch, capsys):
    """sbctl reports the whole ESP; only the produced paths are judged."""
    payload = json.dumps(
        [
            {"file_name": "/efi/EFI/Linux/arch.efi", "is_signed": 1},
            {"file_name": "/efi/EFI/BOOT/BOOTX64.EFI", "is_signed": 0},
        ]
    )
    monkeypatch.setattr(secureboot.subprocess, "run", _sbctl(payload))
    # The .img is not on the ESP and is signed on no setup, so its absence
    # from the report is the normal case rather than a finding.
    rc = secureboot.verify(
        [Path("/efi/EFI/Linux/arch.efi"), Path("/boot/initramfs-linux-fallback.img")]
    )
    assert rc == 0
    assert "sbctl" in capsys.readouterr().out


def test_images_missing_from_the_report_entirely_are_reported(
    sb_on, monkeypatch, capsys
):
    payload = json.dumps([{"file_name": "/efi/shellx64.efi", "is_signed": 1}])
    monkeypatch.setattr(secureboot.subprocess, "run", _sbctl(payload))
    assert secureboot.verify([Path("/efi/EFI/Linux/arch.efi")]) == 0
    assert "arch.efi" in capsys.readouterr().out


def test_an_unusable_answer_is_said_out_loud_without_failing(
    sb_on, monkeypatch, capsys
):
    monkeypatch.setattr(secureboot.subprocess, "run", _sbctl("not json"))
    assert secureboot.verify([Path("/efi/EFI/Linux/arch.efi")]) == 0
    assert capsys.readouterr().out.strip()

    def unavailable(cmd, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(secureboot.subprocess, "run", unavailable)
    assert secureboot.verify([Path("/efi/EFI/Linux/arch.efi")]) == 0
    assert capsys.readouterr().out.strip()

    # A missing sbctl is a different sentence from an unreadable answer.
    monkeypatch.setattr(secureboot.shutil, "which", lambda name: None)
    assert secureboot.verify([Path("/efi/EFI/Linux/arch.efi")]) == 0
    assert "sbctl" in capsys.readouterr().out


def test_configure_fails_when_the_rebuilt_image_is_unsigned(
    kms_env, monkeypatch, capsys
):
    """The reason the check exists: -P succeeded and left an unsigned UKI."""
    conf = kms_env / "mkinitcpio.conf"
    conf.write_text(conf.read_text().replace("nvidia_drm)", "nvidia_drm amdgpu)"))
    monkeypatch.setattr(kms_hook, "ask_yes", lambda q: True)

    preset_dir = kms_env / "root/etc/mkinitcpio.d"
    preset_dir.mkdir(parents=True)
    image = kms_env / "arch.efi"
    image.write_bytes(b"x" * 4096)
    (preset_dir / "linux.preset").write_text(
        f"PRESETS=('default')\ndefault_uki=\"{image}\"\n"
    )

    def fake_backup(path, content, backup=None):
        Path(path).write_text(content, encoding="utf-8")
        return 0, True

    monkeypatch.setattr(sysedit, "write_with_backup", fake_backup)
    monkeypatch.setattr(secureboot, "enabled", lambda: True)
    monkeypatch.setattr(secureboot, "report", lambda: {image: False})

    assert kms_hook.configure() == 1
    out = capsys.readouterr().out
    assert str(image) in out
    # The hook still came out and the undo line is still printed: the image is
    # unsigned, not unbuilt, and the fix is a signature rather than a revert.
    assert "kms" not in mkinitcpio.read_array(conf.read_text(), "HOOKS")
    assert "mkinitcpio.conf.yedek-" in out
