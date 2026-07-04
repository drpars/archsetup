"""Bootloader abstraction, GPU module config and hibernation."""

import pytest

from archsetup.core import bootloader, gpuconfig, hibernate

PARAMS = ["nvidia_drm.modeset=1"]


@pytest.fixture
def boot_paths(tmp_path, monkeypatch, fake_write):
    monkeypatch.setattr(bootloader, "sudo_write", fake_write)
    monkeypatch.setattr(bootloader, "CMDLINE", tmp_path / "no-cmdline")
    monkeypatch.setattr(bootloader, "SDBOOT_ENTRIES", tmp_path / "no-entries")
    monkeypatch.setattr(bootloader, "GRUB_DEFAULT", tmp_path / "no-grub")
    monkeypatch.setattr(bootloader, "GRUB_CFG", tmp_path / "no-grub.cfg")
    monkeypatch.setattr(bootloader, "REFIND_CONF", tmp_path / "no-refind.conf")
    return tmp_path


def test_uki_cmdline(boot_paths, monkeypatch):
    cmdline = boot_paths / "cmdline"
    cmdline.write_text("root=UUID=abc rw quiet\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)

    assert bootloader.detect() == bootloader.UKI
    result = bootloader.add_kernel_params(PARAMS)
    assert result.changed and result.needs_mkinitcpio
    assert cmdline.read_text() == "root=UUID=abc rw quiet nvidia_drm.modeset=1\n"
    assert not bootloader.add_kernel_params(PARAMS).changed


def test_sdboot_entries_skip_fallback(boot_paths, monkeypatch):
    entries = boot_paths / "entries"
    entries.mkdir()
    (entries / "arch.conf").write_text("title Arch\noptions root=UUID=abc rw\n")
    (entries / "arch-fallback.conf").write_text("title F\noptions root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "SDBOOT_ENTRIES", entries)

    assert bootloader.detect() == bootloader.SDBOOT
    assert bootloader.add_kernel_params(PARAMS).changed
    assert "modeset" in (entries / "arch.conf").read_text()
    assert "modeset" not in (entries / "arch-fallback.conf").read_text()


def test_grub_default(boot_paths, monkeypatch):
    grub = boot_paths / "grub"
    grub.write_text('GRUB_CMDLINE_LINUX_DEFAULT="quiet"\nGRUB_CMDLINE_LINUX=""\n')
    grub_cfg = boot_paths / "grub.cfg"
    grub_cfg.write_text("#\n")
    monkeypatch.setattr(bootloader, "GRUB_DEFAULT", grub)
    monkeypatch.setattr(bootloader, "GRUB_CFG", grub_cfg)

    result = bootloader.add_kernel_params(PARAMS)
    assert result.changed
    assert result.regen_cmd == ("sudo", "grub-mkconfig", "-o", str(grub_cfg))
    assert 'GRUB_CMDLINE_LINUX_DEFAULT="quiet nvidia_drm.modeset=1"' in grub.read_text()
    assert 'GRUB_CMDLINE_LINUX=""' in grub.read_text()


def test_refind_lines_and_comments(boot_paths, monkeypatch):
    refind = boot_paths / "refind_linux.conf"
    refind.write_text(
        '"Standard" "root=UUID=x rw quiet"\n"Single" "root=UUID=x rw single"\n# note\n'
    )
    monkeypatch.setattr(bootloader, "REFIND_CONF", refind)

    assert bootloader.add_kernel_params(PARAMS).changed
    text = refind.read_text()
    assert '"Standard" "root=UUID=x rw quiet nvidia_drm.modeset=1"' in text
    assert '"root=UUID=x rw single nvidia_drm.modeset=1"' in text
    assert "# note" in text


def test_unknown_bootloader(boot_paths):
    assert bootloader.detect() == bootloader.UNKNOWN
    assert not bootloader.add_kernel_params(PARAMS).changed
    assert bootloader.info() == 1


def test_replace_prefixes(boot_paths, monkeypatch):
    cmdline = boot_paths / "cmdline"
    cmdline.write_text("root=UUID=abc rw resume=UUID=OLD resume_offset=111\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)

    result = bootloader.add_kernel_params(
        ["resume=UUID=NEW", "resume_offset=222"],
        replace_prefixes=("resume=", "resume_offset="),
    )
    assert result.changed
    tokens = cmdline.read_text().split()
    assert "resume=UUID=NEW" in tokens and "resume_offset=222" in tokens
    assert "resume=UUID=OLD" not in tokens and "resume_offset=111" not in tokens


@pytest.fixture
def gpu_env(boot_paths, tmp_path, monkeypatch, fake_write, runlog):
    monkeypatch.setattr(gpuconfig, "sudo_write", fake_write)
    monkeypatch.setattr(gpuconfig, "run", runlog)
    monkeypatch.setattr(gpuconfig, "MKINITCPIO", tmp_path / "mkinitcpio.conf")
    monkeypatch.setattr(gpuconfig, "NVIDIA_MODPROBE", tmp_path / "nvidia.conf")
    monkeypatch.setattr(gpuconfig, "_nvidia_modeset_is_default", lambda: False)
    return tmp_path


def test_nvidia_modules_full_flow(gpu_env, monkeypatch):
    mk = gpu_env / "mkinitcpio.conf"
    mk.write_text("MODULES=()\n")
    cmdline = gpu_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw modeset=1\n")  # old ineffective param
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)

    assert gpuconfig.configure_nvidia_modules() == 0
    assert "MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)" in mk.read_text()
    assert cmdline.read_text().strip().endswith("nvidia_drm.modeset=1")
    assert gpuconfig.run.calls == [["sudo", "mkinitcpio", "-P"]]


def test_partial_modules_no_duplicates(gpu_env):
    mk = gpu_env / "mkinitcpio.conf"
    mk.write_text("MODULES=(btrfs nvidia)\n")
    gpuconfig.configure_nvidia_modules()
    assert "MODULES=(btrfs nvidia nvidia_modeset nvidia_uvm nvidia_drm)" in mk.read_text()
    assert mk.read_text().count("nvidia ") == 1


def test_nvidia_560_skips_param_steps(gpu_env, monkeypatch):
    monkeypatch.setattr(gpuconfig, "_nvidia_modeset_is_default", lambda: True)
    mk = gpu_env / "mkinitcpio.conf"
    mk.write_text("MODULES=()\n")
    assert gpuconfig.configure_nvidia_modules() == 0
    assert not (gpu_env / "nvidia.conf").exists()


@pytest.fixture
def hib_env(gpu_env, monkeypatch, fake_write, runlog):
    monkeypatch.setattr(hibernate, "sudo_write", fake_write)
    monkeypatch.setattr(hibernate, "run", runlog)
    monkeypatch.setattr(hibernate, "_swapfile_active", lambda: True)
    monkeypatch.setattr(hibernate, "_swap_uuid", lambda: "NEW-UUID")
    monkeypatch.setattr(hibernate, "_swap_offset", lambda: "555555")
    swapfile = gpu_env / "swapfile"
    swapfile.write_text("x")
    monkeypatch.setattr(hibernate, "SWAPFILE", str(swapfile))
    return gpu_env


def test_hibernate_busybox_hooks(hib_env, monkeypatch, runlog):
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw resume=UUID=OLD resume_offset=1\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    (hib_env / "mkinitcpio.conf").write_text("HOOKS=(base udev block filesystems fsck)\n")

    assert hibernate.configure() == 0
    tokens = cmdline.read_text().split()
    assert "resume=UUID=NEW-UUID" in tokens and "resume=UUID=OLD" not in tokens
    assert "resume fsck" in (hib_env / "mkinitcpio.conf").read_text()
    assert hibernate.run.calls == [["sudo", "mkinitcpio", "-P"]]


def test_hibernate_systemd_hook_skipped(hib_env, monkeypatch):
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    (hib_env / "mkinitcpio.conf").write_text("HOOKS=(base systemd block filesystems fsck)\n")

    assert hibernate.configure() == 0
    hooks_line = (hib_env / "mkinitcpio.conf").read_text()
    assert " resume" not in hooks_line


def test_hibernate_requires_swapfile(hib_env, monkeypatch):
    monkeypatch.setattr(hibernate, "SWAPFILE", str(hib_env / "missing"))
    assert hibernate.configure() == 1
