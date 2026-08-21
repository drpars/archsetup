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


def pin_swap(monkeypatch, *areas):
    """Pin what swapon reports, so no test reads the machine under it."""
    monkeypatch.setattr(hibernate, "_swap_areas", lambda: list(areas))


@pytest.fixture
def hib_env(gpu_env, monkeypatch, fake_write, runlog):
    monkeypatch.setattr(hibernate, "sudo_write", fake_write)
    monkeypatch.setattr(hibernate, "run", runlog)
    monkeypatch.setattr(hibernate, "_swap_uuid", lambda path: "NEW-UUID")
    monkeypatch.setattr(hibernate, "_swap_offset", lambda path: "555555")
    # Pinned, or the adequacy check reads the machine under the test: this
    # laptop has 8 GiB of swap against an 11 GiB image target, so the task
    # would stop on a question and the suite would answer it from real stdin.
    pin_swap(monkeypatch, hibernate.SwapArea(str(gpu_env / "swapfile"),
                                             hibernate.FILE, 32 * 2**30))
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


def test_hibernate_requires_some_active_swap(hib_env, monkeypatch):
    pin_swap(monkeypatch)
    assert hibernate.configure() == 1


def test_hibernate_configures_a_swap_partition(hib_env, monkeypatch, capsys):
    """A swap partition is the easier half, and it used to be refused.

    disk.select_partitions() offers one and the installer turns it on, so a
    machine archsetup itself built could reach the one task that exists to
    make it hibernate and be told there is no /swapfile.

    The partition takes no resume_offset=, and must not keep a stale one:
    __find_hibernation_swap_type() matches on
    `device == sis->bdev->bd_dev && first_se(sis)->start_block == offset`,
    and a block device's only extent is add_swap_extent(sis, 0, sis->max, 0)
    -- start_block 0. A leftover offset makes that lookup return -ENODEV.
    """
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw resume=UUID=OLD resume_offset=309248\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    (hib_env / "mkinitcpio.conf").write_text("HOOKS=(base systemd block fsck)\n")
    pin_swap(monkeypatch, hibernate.SwapArea("/dev/nvme0n1p3",
                                             hibernate.PARTITION, 32 * 2**30))
    monkeypatch.setattr(hibernate, "_area_uuid", lambda dev: "PART-UUID")
    # A partition has no offset to derive, so neither of these may be reached.
    monkeypatch.setattr(hibernate, "_swap_offset",
                        lambda path: pytest.fail("bolumde offset aranmamali"))
    monkeypatch.setattr(hibernate, "_ensure_offset_tool",
                        lambda path: pytest.fail("bolumde arac kurulmamali"))

    assert hibernate.configure() == 0
    tokens = cmdline.read_text().split()
    assert "resume=UUID=PART-UUID" in tokens
    assert not [tok for tok in tokens if tok.startswith("resume_offset=")]
    assert "/dev/nvme0n1p3" in capsys.readouterr().out


def test_hibernate_will_not_guess_between_swap_areas(hib_env, monkeypatch, capsys):
    """Two areas and neither is ours: the kernel uses one, so someone must say which.

    The image is not spread over whatever is mounted. swsusp_swap_check()
    resolves resume= to a single swap type and every page goes through
    alloc_swapdev_block(root_swap), so naming the wrong device here is
    exactly the failure this task exists to prevent.
    """
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    pin_swap(
        monkeypatch,
        hibernate.SwapArea("/dev/sda2", hibernate.PARTITION, 8 * 2**30),
        hibernate.SwapArea("/var/swap", hibernate.FILE, 32 * 2**30),
    )

    assert hibernate.configure() == 1
    assert "resume=" not in cmdline.read_text()
    out = capsys.readouterr().out
    assert "/dev/sda2" in out and "/var/swap" in out


def test_hibernate_prefers_the_swapfile_it_made(hib_env, monkeypatch):
    """With a partition alongside, /swapfile is the one this tool sizes."""
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    (hib_env / "mkinitcpio.conf").write_text("HOOKS=(base systemd block fsck)\n")
    pin_swap(
        monkeypatch,
        hibernate.SwapArea("/dev/sda2", hibernate.PARTITION, 8 * 2**30),
        hibernate.SwapArea(str(hib_env / "swapfile"), hibernate.FILE, 32 * 2**30),
    )
    monkeypatch.setattr(hibernate, "SWAPFILE", str(hib_env / "swapfile"))

    assert hibernate.configure() == 0
    tokens = cmdline.read_text().split()
    assert "resume=UUID=NEW-UUID" in tokens and "resume_offset=555555" in tokens


def test_hibernate_measures_the_resume_area_not_every_area(hib_env, monkeypatch):
    """A big second area does not make a small resume target adequate.

    This check used to sum every active area, on the belief that the image
    goes wherever the kernel finds room. It does not: enough_swap() asks
    count_swap_pages(root_swap, 1) about the resume device alone. Summed,
    the 40 GiB here would have passed silently against an 11 GiB image.
    """
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    pin_swap(
        monkeypatch,
        hibernate.SwapArea(str(hib_env / "swapfile"), hibernate.FILE, 8 * 2**30),
        hibernate.SwapArea("/dev/sda2", hibernate.PARTITION, 32 * 2**30),
    )
    monkeypatch.setattr(hibernate, "SWAPFILE", str(hib_env / "swapfile"))
    monkeypatch.setattr(hibernate, "_image_size", lambda: 11 * 2**30)
    asked: list[str] = []
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: bool(asked.append(prompt)))

    assert hibernate.configure() == 0
    assert asked, "kucuk resume hedefi sorulmadan gecti"
    assert "resume=" not in cmdline.read_text()


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
    pin_swap(monkeypatch, hibernate.SwapArea(str(hib_env / "swapfile"),
                                             hibernate.FILE, 8 * 2**30))
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
    pin_swap(monkeypatch, hibernate.SwapArea(str(hib_env / "swapfile"),
                                             hibernate.FILE, 8 * 2**30))
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


# --- removal: the half the module header was missing ------------------------


def test_remove_kernel_params_drops_them(boot_paths, monkeypatch):
    """Removal is not a second mechanism, it is _merge() with nothing to add."""
    cmdline = boot_paths / "cmdline"
    cmdline.write_text("root=UUID=abc rw resume=UUID=OLD resume_offset=309248 quiet\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)

    result = bootloader.remove_kernel_params(("resume=", "resume_offset="))
    assert result.changed and result.needs_mkinitcpio
    assert cmdline.read_text().split() == ["root=UUID=abc", "rw", "quiet"]


def test_remove_kernel_params_is_a_no_op_when_absent(boot_paths, monkeypatch, capsys):
    """And it says so in the sentence that fits: absent, not "already set"."""
    cmdline = boot_paths / "cmdline"
    cmdline.write_text("root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)

    assert bootloader.remove_kernel_params(("resume=", "resume_offset=")).changed is False
    assert cmdline.read_text() == "root=UUID=abc rw\n"
    assert "resume=" in capsys.readouterr().out


def test_removal_does_not_invent_an_options_line(boot_paths, monkeypatch):
    """An entry with no options line has nothing to remove.

    Writing one would put an empty `options ` into a file that was correct
    as it stood.
    """
    entries = boot_paths / "entries"
    entries.mkdir(exist_ok=True)
    entry = entries / "arch.conf"
    entry.write_text("title Arch\nlinux /vmlinuz-linux\n")
    monkeypatch.setattr(bootloader, "CMDLINE", boot_paths / "absent")
    monkeypatch.setattr(bootloader, "SDBOOT_ENTRIES", entries)

    assert bootloader.remove_kernel_params(("resume=",)).changed is False
    assert "options" not in entry.read_text()


def test_btrfs_offset_comes_from_map_swapfile(monkeypatch):
    """filefrag and map-swapfile disagree on btrfs, and only one is right.

    Measured on a loopback btrfs holding one NOCOW swapfile: filefrag's first
    extent said 86880 where `btrfs inspect-internal map-swapfile -r` said
    115136. Trusting filefrag there writes a plausible, wrong resume_offset
    and nothing reports it until a resume that does not come back.
    """
    seen = []

    class Out:
        def __init__(self, text):
            self.stdout = text

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        if cmd[0] == "findmnt":
            return Out("btrfs\n")
        if "map-swapfile" in cmd:
            return Out("115136\n")
        return Out(" 0:  0.. 32767: 86880.. 119647: 32768: last,eof\n")

    monkeypatch.setattr(hibernate.subprocess, "run", fake_run)
    assert hibernate._swap_offset("/swapfile") == "115136"
    assert not any("filefrag" in c for c in seen)


def test_ext4_offset_still_comes_from_filefrag(monkeypatch):
    """Measured on this laptop: 309248, the number resume_offset wants."""

    class Out:
        def __init__(self, text):
            self.stdout = text

    def fake_run(cmd, **kwargs):
        if cmd[0] == "findmnt":
            return Out("ext4\n")
        return Out(" 0:  0.. 32767: 309248.. 342015: 32768: last,eof\n")

    monkeypatch.setattr(hibernate.subprocess, "run", fake_run)
    assert hibernate._swap_offset("/swapfile") == "309248"


@pytest.fixture
def rm_env(hib_env, monkeypatch, runlog):
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: True)
    monkeypatch.setattr(hibernate, "_ensure_offset_tool", lambda path: True)
    return hib_env


def test_remove_takes_the_file_and_the_parameters(rm_env, monkeypatch, runlog):
    cmdline = rm_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw resume=UUID=OLD resume_offset=1 quiet\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    (rm_env / "mkinitcpio.conf").write_text("HOOKS=(base udev resume fsck)\n")

    assert hibernate.remove() == 0
    assert cmdline.read_text().split() == ["root=UUID=abc", "rw", "quiet"]
    assert "resume" not in (rm_env / "mkinitcpio.conf").read_text()
    assert ["sudo", "swapoff", hibernate.SWAPFILE] in runlog.calls
    assert ["sudo", "rm", "-f", hibernate.SWAPFILE] in runlog.calls


def test_remove_updates_boot_config_before_deleting_the_file(rm_env, monkeypatch, runlog):
    """The dangerous state is a boot config pointing at a file that is gone.

    Deleting first leaves it if the rebuild then fails; this order leaves
    hibernation merely unconfigured, which costs a feature and not a boot.
    """
    cmdline = rm_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw resume=UUID=OLD resume_offset=1\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    (rm_env / "mkinitcpio.conf").write_text("HOOKS=(base systemd fsck)\n")

    assert hibernate.remove() == 0
    names = [c for c in runlog.calls]
    rebuild = names.index(["sudo", "mkinitcpio", "-P"])
    delete = names.index(["sudo", "rm", "-f", hibernate.SWAPFILE])
    assert rebuild < delete


def test_remove_cleans_parameters_a_hand_deletion_left(rm_env, monkeypatch, runlog):
    """The state worth being able to clean: file gone, resume= still there."""
    cmdline = rm_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw resume=UUID=OLD resume_offset=1\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    monkeypatch.setattr(hibernate, "SWAPFILE", str(rm_env / "gone"))
    (rm_env / "mkinitcpio.conf").write_text("HOOKS=(base systemd fsck)\n")

    assert hibernate.remove() == 0
    assert "resume" not in cmdline.read_text()
    assert not [c for c in runlog.calls if c[:2] == ["sudo", "rm"]]


def test_remove_does_not_offer_to_delete_a_file_that_is_not_there(rm_env, monkeypatch):
    """A swap partition has no file to take away, and the prompt must not claim one.

    remove() never touches a device, so on a partition machine what it undoes
    is the configuration. The same branch covers a hand-deleted swapfile.
    Asserted on the message key rather than its text, so it holds in either
    locale.
    """
    cmdline = rm_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw resume=UUID=OLD resume_offset=1\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    monkeypatch.setattr(hibernate, "SWAPFILE", str(rm_env / "gone"))
    (rm_env / "mkinitcpio.conf").write_text("HOOKS=(base systemd fsck)\n")
    keys: list[str] = []
    spoken = hibernate.t
    monkeypatch.setattr(
        hibernate, "t", lambda key, **fmt: keys.append(key) or spoken(key, **fmt)
    )

    assert hibernate.remove() == 0
    assert "msg.swap_remove_plan_params" in keys
    assert "msg.swap_remove_plan" not in keys
    assert "msg.swap_params_removed" in keys
    assert "msg.swap_removed" not in keys


def test_remove_does_nothing_when_there_is_nothing_to_remove(rm_env, monkeypatch, runlog):
    cmdline = rm_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    monkeypatch.setattr(hibernate, "SWAPFILE", str(rm_env / "gone"))
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: pytest.fail("sorulmamali"))

    assert hibernate.remove() == 0
    assert runlog.calls == []


# --- resize -----------------------------------------------------------------


@pytest.fixture
def resize_env(hib_env, monkeypatch, runlog):
    swapfile = hib_env / "swapfile"
    swapfile.write_bytes(b"x" * (64 * 2**20))
    monkeypatch.setattr(hibernate, "SWAPFILE", str(swapfile))
    monkeypatch.setattr(hibernate, "_ensure_offset_tool", lambda path: True)
    monkeypatch.setattr(hibernate, "_free_bytes", lambda: 512 * 2**20)
    monkeypatch.setattr(hibernate.hardware, "ram_bytes", lambda: 0)
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: True)
    cmdline = hib_env / "cmdline"
    cmdline.write_text("root=UUID=abc rw resume=UUID=OLD resume_offset=1\n")
    monkeypatch.setattr(bootloader, "CMDLINE", cmdline)
    (hib_env / "mkinitcpio.conf").write_text("HOOKS=(base systemd fsck)\n")
    return hib_env


def _answer(monkeypatch, value):
    monkeypatch.setattr(hibernate, "input", lambda prompt="": value, raising=False)


def test_resize_grows_with_fallocate_and_rewrites_the_offset(resize_env, monkeypatch, runlog):
    """The size is the easy half; the offset is the half a hand-run guesses."""
    _answer(monkeypatch, "192")
    monkeypatch.setattr(hibernate, "_swap_offset", lambda path: "778899")

    assert hibernate.resize() == 0
    assert ["sudo", "fallocate", "-l", "192M", hibernate.SWAPFILE] in runlog.calls
    assert ["sudo", "mkswap", hibernate.SWAPFILE] in runlog.calls
    assert "resume_offset=778899" in (resize_env / "cmdline").read_text()


def test_resize_shrinks_with_truncate(resize_env, monkeypatch, runlog):
    """Measured: `fallocate -l` smaller than the file returns 0 and does nothing.

    192M file, `fallocate -l 64M`, rc=0, size still 201326592 -- so a shrink
    written with fallocate would report success and leave the old size.
    """
    _answer(monkeypatch, "32")

    assert hibernate.resize() == 0
    assert ["sudo", "truncate", "-s", "32M", hibernate.SWAPFILE] in runlog.calls
    assert not [c for c in runlog.calls if c[1] == "fallocate"]


def test_resize_refuses_above_the_ceiling(resize_env, monkeypatch, runlog):
    """The file's own space plus what is free; growing needs the difference."""
    _answer(monkeypatch, "9000")

    assert hibernate.resize() == 1
    assert not [c for c in runlog.calls if c[1] in ("fallocate", "truncate")]


def test_resize_below_ram_asks(resize_env, monkeypatch, runlog):
    monkeypatch.setattr(hibernate.hardware, "ram_bytes", lambda: 128 * 2**20)
    monkeypatch.setattr(hibernate, "ask_yes", lambda prompt: False)
    _answer(monkeypatch, "96")

    assert hibernate.resize() == 0
    assert not [c for c in runlog.calls if c[1] in ("fallocate", "truncate")]


def test_resize_default_is_the_current_size(resize_env, monkeypatch, runlog):
    """Enter has to be the answer that changes nothing.

    Caught by a real run rather than by this suite: on the laptop this was
    written on, swap is 28672 MiB against 27807 MiB of RAM, and a RAM-shaped
    default meant pressing enter would quietly shrink it.
    """
    monkeypatch.setattr(hibernate.hardware, "ram_bytes", lambda: 128 * 2**20)
    _answer(monkeypatch, "")

    assert hibernate.resize() == 0
    assert not [c for c in runlog.calls if c[1] in ("fallocate", "truncate")]
