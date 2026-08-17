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
    # Pinned, or the adequacy check reads the machine under the test: this
    # laptop has 8 GiB of swap against an 11 GiB image target, so the task
    # would stop on a question and the suite would answer it from real stdin.
    monkeypatch.setattr(hibernate, "_swap_bytes", lambda: 32 * 2**30)
    monkeypatch.setattr(hibernate, "_image_size", lambda: 11 * 2**30)
    # Pinned for the same reason: unpinned, the NVIDIA gate reads the real
    # /etc/mkinitcpio.conf.d and the suite answers from the machine under it.
    # Empty here today, which is exactly how this class of leak stays invisible.
    monkeypatch.setattr(hibernate, "CONF_D", gpu_env / "mkinitcpio.conf.d")
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


def test_hibernate_stops_when_swap_cannot_hold_the_image(hib_env, monkeypatch, capsys):
    """Swap under the kernel's own image target is a measured negative.

    It never surfaces as a refusal at hibernate time -- measured on this
    laptop, systemd's precheck passed, the kernel entered hibernation and
    the machine cold-booted 2.5 minutes later. So the task asks, and a run
    with no one to answer takes the safe side.
    """
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    monkeypatch.setattr(hibernate, "_swap_bytes", lambda: 8 * 2**30)
    monkeypatch.setattr(hibernate, "_image_size", lambda: 11 * 2**30)
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: False)

    assert hibernate.configure() == 0
    assert "resume=" not in cmdline.read_text()
    assert "8192" in capsys.readouterr().out


def test_hibernate_proceeds_when_the_answer_is_yes(hib_env, monkeypatch):
    """A lean machine can still hibernate; the check is a question."""
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    (hib_env / "mkinitcpio.conf").write_text("HOOKS=(base systemd block fsck)\n")
    monkeypatch.setattr(hibernate, "_swap_bytes", lambda: 8 * 2**30)
    monkeypatch.setattr(hibernate, "_image_size", lambda: 11 * 2**30)
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: True)

    assert hibernate.configure() == 0
    assert "resume=UUID=NEW-UUID" in cmdline.read_text()


def test_hibernate_does_not_ask_when_swap_cannot_be_measured(hib_env, monkeypatch):
    """An unreadable /sys/power/image_size is not evidence of a problem."""
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    (hib_env / "mkinitcpio.conf").write_text("HOOKS=(base systemd block fsck)\n")
    monkeypatch.setattr(hibernate, "_image_size", lambda: 0)
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: pytest.fail("sorulmamali"))

    assert hibernate.configure() == 0
    assert "resume=UUID=NEW-UUID" in cmdline.read_text()


@pytest.fixture
def nv_env(hib_env, monkeypatch):
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    return hib_env


def test_hibernate_offers_to_drop_nvidia_from_modules(nv_env, monkeypatch, capsys):
    """NVIDIA in the resume kernel refuses to freeze, so the image never lands.

    Measured on this laptop: the image was read back at full speed and then
    nv_pmops_freeze returned -5, because no systemd unit has written
    /proc/driver/nvidia/suspend inside an initramfs. Taking the modules out
    made the same machine hibernate and return on the same boot id.
    """
    mk = nv_env / "mkinitcpio.conf"
    mk.write_text("MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm amdgpu)\nHOOKS=(base systemd block fsck)\n")
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: True)

    assert hibernate.configure() == 0
    assert "MODULES=(amdgpu)" in mk.read_text()
    assert "resume=UUID=NEW-UUID" in (nv_env / "cmdline").read_text()
    assert hibernate.run.calls == [["sudo", "mkinitcpio", "-P"]]
    assert "nv_pmops_freeze" in capsys.readouterr().out


def test_hibernate_keeps_nvidia_when_the_answer_is_no(nv_env, monkeypatch):
    """A refusal leaves the machine as it was; the parameters are still written.

    Early KMS is worth having to someone who never hibernates, so this is a
    question, not a removal -- and a run with no one to answer takes "no".
    """
    mk = nv_env / "mkinitcpio.conf"
    mk.write_text("MODULES=(nvidia amdgpu)\nHOOKS=(base systemd block fsck)\n")
    asked: list[str] = []
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: bool(asked.append(prompt)))

    assert hibernate.configure() == 0
    # Asserting only that nothing changed would pass with the gate deleted;
    # what makes this a guard is that the question was put at all.
    assert asked, "kapı hiç sormadı"
    assert "MODULES=(nvidia amdgpu)" in mk.read_text()
    assert "resume=UUID=NEW-UUID" in (nv_env / "cmdline").read_text()


def test_hibernate_does_not_ask_without_nvidia_modules(nv_env, monkeypatch):
    """amdgpu alone freezes fine; the gate fires on a measured negative only."""
    mk = nv_env / "mkinitcpio.conf"
    mk.write_text("MODULES=(amdgpu)\nHOOKS=(base systemd block fsck)\n")
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: pytest.fail("sorulmamali"))

    assert hibernate.configure() == 0
    assert "MODULES=(amdgpu)" in mk.read_text()


def test_hibernate_will_not_rewrite_a_drop_in_it_does_not_own(nv_env, monkeypatch, capsys):
    """A drop-in wins MODULES, and editing the main file would not remove it.

    The gate has to read the effective text to see the modules at all, but
    what it may write back is only the main file -- so when the modules come
    from a drop-in it says so rather than making a change that does nothing.
    """
    mk = nv_env / "mkinitcpio.conf"
    mk.write_text("MODULES=(amdgpu)\nHOOKS=(base systemd block fsck)\n")
    conf_d = nv_env / "mkinitcpio.conf.d"
    conf_d.mkdir()
    (conf_d / "10-nvidia.conf").write_text("MODULES=(amdgpu nvidia nvidia_drm)\n")
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: True)

    assert hibernate.configure() == 0
    assert "MODULES=(amdgpu)" in mk.read_text()
    assert "MODULES=(amdgpu nvidia nvidia_drm)" in (conf_d / "10-nvidia.conf").read_text()
    assert "drop-in" in capsys.readouterr().out
