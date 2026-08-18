"""Target system configuration via arch-chroot.

Replaces the old script's copy-itself-into-/mnt trick: every step is a
direct file edit under /mnt or an `arch-chroot /mnt <command>` call.
User passwords are set interactively with passwd inside the chroot, so
they never pass through shell pipes or variables.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from pathlib import Path

from ..core import hardware, i18n, mkinitcpio
from ..core.pacman import run
from ..core.prompt import ask_yes

t = i18n.t

MNT = Path("/mnt")
ESP = MNT / "efi"
DEFAULT_KEYMAP = "trq"
DEFAULT_LOCALE = "tr_TR"
DEFAULT_TIMEZONE = "Europe/Istanbul"

# ter-v16b is unreadably small on a HiDPI panel; v22b is the comfortable
# middle of the range. All of these ship in terminus-font.
CONSOLE_FONTS = ("ter-v16b", "ter-v20b", "ter-v22b", "ter-v24b", "ter-v28b", "ter-v32b")
DEFAULT_FONT = "ter-v22b"


def chroot_run(args: list[str]) -> int:
    return run(["arch-chroot", str(MNT), *args])


def chroot_install(repo_pkgs: list[str], aur_pkgs: list[str]) -> int:
    if aur_pkgs:
        print(t("inst.no_aur_in_target", pkgs=" ".join(aur_pkgs)))
    if not repo_pkgs:
        return 0
    return chroot_run(["pacman", "-S", "--needed", "--noconfirm", *repo_pkgs])


def target_ready() -> bool:
    if (MNT / "etc").is_dir() and (MNT / "usr").is_dir():
        return True
    print(t("inst.no_target"))
    return False


def _editor() -> str:
    return os.environ.get("EDITOR", "nvim")


def edit_file(path: str) -> int:
    return subprocess.call([_editor(), path])


def set_hostname() -> int:
    if not target_ready():
        return 1
    hostname = input(f"{t('inst.hostname_q')} [archlinux]: ").strip() or "archlinux"
    (MNT / "etc/hostname").write_text(hostname + "\n", encoding="utf-8")
    print(f"/mnt/etc/hostname <- {hostname}")
    return 0


def _pick_font() -> str:
    default_index = CONSOLE_FONTS.index(DEFAULT_FONT) + 1
    print(f"\n{t('inst.font_q')}")
    for index, font in enumerate(CONSOLE_FONTS, 1):
        print(f"  {index}) {font}")
    raw = input(f"{t('inst.choice')} [{default_index}]: ").strip() or str(default_index)
    if raw.isdigit() and 1 <= int(raw) <= len(CONSOLE_FONTS):
        return CONSOLE_FONTS[int(raw) - 1]
    print(t("inst.invalid"))
    return DEFAULT_FONT


def set_vconsole(keymap: str | None = None) -> int:
    if not target_ready():
        return 1
    if keymap is None:
        keymap = (
            input(f"{t('inst.keymap_q')} [{DEFAULT_KEYMAP}]: ").strip()
            or DEFAULT_KEYMAP
        )
    lines = [f"KEYMAP={keymap}"]
    if ask_yes(t("inst.terminus_q")):
        lines.append(f"FONT={_pick_font()}")
    (MNT / "etc/vconsole.conf").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"/mnt/etc/vconsole.conf <- {' '.join(lines)}")
    return 0


def set_locale(locale: str | None = None) -> int:
    if not target_ready():
        return 1
    if locale is None:
        locale = (
            input(f"{t('inst.locale_q')} [{DEFAULT_LOCALE}]: ").strip()
            or DEFAULT_LOCALE
        )
    # The prompt says .UTF-8 is appended, but typing the full name is the
    # natural thing to do -- and it used to build "tr_TR.UTF-8.UTF-8", which
    # matches nothing in locale.gen and fails the step outright. Accept both
    # spellings instead of being right about whose fault it was.
    locale = re.sub(r"\.utf-?8$", "", locale, flags=re.IGNORECASE)
    (MNT / "etc/locale.conf").write_text(
        f"LANG={locale}.UTF-8\nLC_COLLATE=C\n", encoding="utf-8"
    )
    gen = MNT / "etc/locale.gen"
    text = gen.read_text(encoding="utf-8")
    new_text = re.sub(
        rf"^#\s*({re.escape(locale)}\.UTF-8.*)$", r"\1", text, flags=re.MULTILINE
    )
    if new_text == text and f"\n{locale}.UTF-8" not in text:
        print(t("inst.locale_missing", locale=locale))
        return 1
    gen.write_text(new_text, encoding="utf-8")
    return chroot_run(["locale-gen"])


def set_timezone(timezone: str | None = None) -> int:
    if not target_ready():
        return 1
    if timezone is None:
        timezone = (
            input(f"{t('inst.tz_q')} [{DEFAULT_TIMEZONE}]: ").strip()
            or DEFAULT_TIMEZONE
        )
    if not (MNT / "usr/share/zoneinfo" / timezone).is_file():
        print(t("inst.tz_invalid", tz=timezone))
        return 1
    localtime = MNT / "etc/localtime"
    localtime.unlink(missing_ok=True)
    localtime.symlink_to(f"/usr/share/zoneinfo/{timezone}")
    rc = run(["systemctl", "--root", str(MNT), "enable", "systemd-timesyncd"])
    if ask_yes(t("inst.utc_q")):
        rc |= chroot_run(["hwclock", "--systohc", "--utc"])
    else:
        rc |= chroot_run(["hwclock", "--systohc", "--localtime"])
    return rc


def _passwd_loop(username: str) -> int:
    for _ in range(3):
        if chroot_run(["passwd", username]) == 0:
            return 0
    return 1


def set_root_password() -> int:
    if not target_ready():
        return 1
    return _passwd_loop("root")


def add_user() -> int:
    if not target_ready():
        return 1
    while True:
        username = input(f"{t('inst.user_q')}: ").strip()
        if re.fullmatch(r"[a-z_][a-z0-9_-]{2,31}", username):
            break
        print(t("inst.user_invalid"))

    rc = chroot_run(["useradd", "-m", username])
    if rc != 0:
        return rc
    rc |= _passwd_loop(username)

    if ask_yes(t("inst.sudo_q", user=username)):
        sudoers = MNT / "etc/sudoers"
        text = sudoers.read_text(encoding="utf-8")
        sudoers.write_text(
            text.replace("# %wheel ALL=(ALL:ALL) ALL", "%wheel ALL=(ALL:ALL) ALL", 1),
            encoding="utf-8",
        )
        rc |= chroot_run(["usermod", "-aG", "wheel", username])
    return rc


# Only reached when MemTotal cannot be read. The old unconditional default,
# kept as the fallback so a machine that cannot be measured still gets the
# behaviour it used to get.
SWAP_FALLBACK_MIB = 8192


def ram_mib() -> int | None:
    """MemTotal in MiB -- the live ISO runs on the machine being installed."""
    return (hardware.ram_bytes() // 2**20) or None


def _target_free_mib() -> int:
    """Free megabytes on the filesystem that will hold the swapfile, or 0.

    The root of the target, not the ESP: /swapfile lands next to /, and
    _esp_free_mb() above answers a different question about a different
    filesystem.
    """
    try:
        st = os.statvfs(MNT)
    except OSError:
        return 0
    return (st.f_bavail * st.f_frsize) // (1024 * 1024)


def _fstype(path: Path) -> str:
    out = subprocess.run(
        ["findmnt", "-no", "FSTYPE", "-T", str(path)], capture_output=True, text=True
    )
    return out.stdout.strip()


def _write_swapfile(path: Path, size_mib: int) -> int:
    """Create the swapfile the way the target filesystem needs it.

    Two filesystem facts, both measured on loopback images (kernel
    7.1.8-zen1-3-zen, btrfs-progs v7.1):

    - On btrfs a copy-on-write file cannot be swap. This step's own sequence
      -- dd, then mkswap, then swapon -- returns 0, 0 and then EINVAL, which
      means every btrfs install this installer ever performed produced a
      swapfile it could not turn on. `chattr +C` fixes it, but only on a file
      with nothing in it yet: on a file that already holds bytes it returns 0,
      sets no flag (lsattr shows none) and swapon still fails. So the flag
      goes on an empty file, before anything is written, and the old file is
      removed rather than reused for the same reason.
    - fallocate produced a working swapfile on both ext4 and btrfs (mkswap and
      swapon both 0) without writing the bytes, which on a RAM-sized swapfile
      is the difference between an instant step and writing tens of gigabytes.
      dd stays as the fallback because select_partitions() also offers ext2,
      ext3 and jfs, where fallocate is not supported -- and its exit code is
      a cheaper way to find that out than a table of filesystems to maintain.
    """
    rc = run(["rm", "-f", str(path)])
    rc |= run(["touch", str(path)])
    if _fstype(path.parent) == "btrfs":
        rc |= run(["chattr", "+C", str(path)])
    if run(["fallocate", "-l", f"{size_mib}M", str(path)]) != 0:
        print(t("inst.swap_fallocate_fallback"))
        rc |= run(["dd", "if=/dev/zero", f"of={path}", "bs=1M",
                   f"count={size_mib}", "status=progress"])
    rc |= run(["chmod", "600", str(path)])
    rc |= run(["mkswap", str(path)])
    rc |= run(["swapon", str(path)])
    return rc


def create_swapfile() -> int:
    """Size the swapfile from RAM, because hibernation is what needs it.

    The default used to be a flat 8192 MiB whatever the machine had, and
    archsetup writes resume=/resume_offset= unconditionally -- so it
    promised hibernation and then sized the one thing hibernation depends
    on by a constant. Measured on a 27.2 GiB laptop: swap 8.0 GiB against
    /sys/power/image_size 10.76 GiB, i.e. the image the kernel *aims* for
    already did not fit, and hibernation there entered and never came back.

    RAM is the default rather than 2/5 of it. 2/5 is what the kernel targets
    (measured: image_size/MemTotal = 0.396) but it is a target, not a bound
    -- what does not compress down stays in the image. The number is shown
    and can be overridden; guessing low is what fails silently.
    """
    if not target_ready():
        return 1
    ram = ram_mib()
    default = ram or SWAP_FALLBACK_MIB
    free = _target_free_mib()
    if ram:
        print(t("inst.swap_sizing", ram=ram, target=ram * 2 // 5))
    if free:
        print(t("inst.swap_ceiling", free=free))
    raw = input(f"{t('inst.swapsize_q')} [{default}]: ").strip() or str(default)
    if not raw.isdigit() or int(raw) < 1:
        print(t("inst.invalid"))
        return 1

    size = int(raw)
    # The ceiling is measured rather than trusted: dd into a full filesystem
    # fails partway and leaves a short swapfile that mkswap will happily
    # accept, so the number is checked before anything is written.
    if free and size > free:
        print(t("inst.swap_too_big", want=size, free=free))
        return 1
    # Below RAM is allowed but not silent. RAM is the only size that
    # guarantees hibernation -- the image cannot exceed what is in memory,
    # and how far it compresses is a property of the workload, not of the
    # install -- so a smaller number is a bet, and the person making it
    # should be the one told what it buys.
    if ram and size < ram:
        print(t("inst.swap_below_ram", want=size, ram=ram))
        if not ask_yes(t("inst.swap_below_ram_q")):
            print(t("inst.cancelled"))
            return 1
    return _write_swapfile(MNT / "swapfile", size)


def _presets() -> list[Path]:
    return mkinitcpio.presets(MNT)


def _choose_preset() -> Path | None:
    presets = _presets()
    if not presets:
        print(t("inst.no_preset"))
        return None
    if len(presets) == 1:
        return presets[0]
    for index, preset in enumerate(presets, 1):
        print(f"  {index}) {preset.stem}")
    raw = input(f"{t('inst.choice')} [1]: ").strip() or "1"
    if raw.isdigit() and 1 <= int(raw) <= len(presets):
        return presets[int(raw) - 1]
    print(t("inst.invalid"))
    return None


def default_uki_path(preset: Path) -> str | None:
    return mkinitcpio.preset_value(preset.read_text(encoding="utf-8"), "default_uki")


@contextlib.contextmanager
def _target_bootloader():
    """Point core.bootloader at the target's files under /mnt.

    Installer runs as root, so writes are direct instead of sudo tee.
    """
    from ..core import bootloader

    saved = (
        bootloader.CMDLINE, bootloader.SDBOOT_ENTRIES, bootloader.GRUB_DEFAULT,
        bootloader.GRUB_CFG, bootloader.REFIND_CONF, bootloader.sudo_write,
    )

    def direct_write(path, content) -> int:
        Path(path).write_text(content, encoding="utf-8")
        return 0

    bootloader.CMDLINE = MNT / "etc/kernel/cmdline"
    bootloader.SDBOOT_ENTRIES = MNT / "boot/loader/entries"
    bootloader.GRUB_DEFAULT = MNT / "etc/default/grub"
    bootloader.GRUB_CFG = MNT / "boot/grub/grub.cfg"
    bootloader.REFIND_CONF = MNT / "boot/refind_linux.conf"
    bootloader.sudo_write = direct_write
    try:
        yield bootloader
    finally:
        (
            bootloader.CMDLINE, bootloader.SDBOOT_ENTRIES, bootloader.GRUB_DEFAULT,
            bootloader.GRUB_CFG, bootloader.REFIND_CONF, bootloader.sudo_write,
        ) = saved


FALLBACK_PRESETS = "PRESETS=('default' 'fallback')"
DEFAULT_ONLY_PRESETS = "PRESETS=('default')"

# A fallback UKI is far larger than the normal one -- measured on a plain
# linux-zen install, 214 MB against 33 MB -- because -S autodetect drops the
# hardware filter and bundles every module and its firmware. One kernel plus
# its fallback therefore wants roughly a quarter of a gigabyte, and two
# kernels will not fit an ESP that was sized when 512 MB looked generous.
FALLBACK_NEEDS_MB = 512


def _esp_free_mb() -> int | None:
    """Free megabytes on the target ESP, or None if it cannot be measured."""
    try:
        st = os.statvfs(MNT / "efi")
    except OSError:
        return None
    return (st.f_bavail * st.f_frsize) // (1024 * 1024)


def _want_fallback() -> bool:
    """Whether to build a fallback UKI, asking only when space is tight.

    Silently skipping it would take away the rescue image; silently building
    it can fill the ESP and leave the *next* kernel update with nowhere to
    write, which surfaces much later and much less clearly. So the decision
    is only handed over when the numbers say it is a real decision.
    """
    free = _esp_free_mb()
    if free is None or free >= FALLBACK_NEEDS_MB:
        return True
    print(t("inst.fallback_tight", free=free, needed=FALLBACK_NEEDS_MB))
    return ask_yes(t("inst.fallback_anyway_q"))


def _enable_fallback_preset(text: str) -> str:
    """Leave exactly one active PRESETS line, holding default *and* fallback.

    A fallback image is the only thing to boot when the default one comes out
    broken, which is precisely when a rescue entry is needed. It is built with
    `-S autodetect`, so it carries every module rather than only the hardware
    present at build time -- that is also what keeps the disk bootable after
    it moves to another machine or into a VM.

    Preset files disagree on the stock layout: some carry PRESETS=('default')
    active with the pair commented out beneath it, others ship the pair active
    already. Commenting every live PRESETS line first and then activating the
    pair gives the same result for both, instead of leaving two assignments
    whose order silently decides the outcome. Nothing here reads the kernel's
    name, which is what keeps it working for a package we have never seen.

    Do not assume the kernel package puts the file there. Measured 2026-08-13:
    neither linux-g14 nor linux-ogc ships /etc/mkinitcpio.d/*.preset at all --
    on this machine that file belongs to no package -- because modern kernels
    hand the job to /usr/lib/kernel/install.d/ instead. A target installed with
    such a kernel and no preset lands in _choose_preset()'s empty branch.
    """
    return _force_presets(text, FALLBACK_PRESETS)


def _default_only_preset(text: str) -> str:
    """Leave only the default preset — used when the ESP cannot hold both."""
    return _force_presets(text, DEFAULT_ONLY_PRESETS)


def _force_presets(text: str, wanted: str) -> str:
    text = re.sub(r"^(PRESETS=)", r"#\1", text, flags=re.MULTILINE)
    revived, count = re.subn(
        rf"^#({re.escape(wanted)})", r"\1", text, count=1, flags=re.MULTILINE
    )
    if count:
        return revived
    # No commented line to revive (a preset written by hand, say).
    return f"{text.rstrip()}\n{wanted}\n"


def gen_uki() -> int:
    """Switch the target to systemd initramfs + Unified Kernel Image output."""
    if not target_ready():
        return 1
    if not (MNT / "etc/kernel/cmdline").is_file():
        # UKI embeds the cmdline; without it the image cannot find root.
        print(t("inst.no_cmdline"))
        return 1
    preset = _choose_preset()
    if preset is None:
        return 1

    mkconf = MNT / "etc/mkinitcpio.conf"
    mkconf.write_text(
        mkconf.read_text(encoding="utf-8").replace("base udev", "base systemd", 1),
        encoding="utf-8",
    )

    fallback = _want_fallback()

    text = preset.read_text(encoding="utf-8")
    names = ("default", "fallback") if fallback else ("default",)
    text = mkinitcpio.set_uki_output(text, names)
    text = _enable_fallback_preset(text) if fallback else _default_only_preset(text)
    preset.write_text(text, encoding="utf-8")

    uki = default_uki_path(preset)
    if uki is None:
        print(t("inst.no_uki_line", preset=preset))
        return 1
    rc = chroot_run(["mkdir", "-p", str(Path(uki).parent)])
    rc |= chroot_run(["mkinitcpio", "-P"])
    return rc


def disable_watchdog() -> int:
    """Blacklist watchdog modules and add the matching kernel parameter.

    The parameter goes through core.bootloader pointed at /mnt, so it
    lands correctly for UKI, GRUB or rEFInd targets alike.
    """
    if not target_ready():
        return 1

    if hardware.cpu_matches("intel"):
        param = "modprobe.blacklist=iTCO_wdt"
    elif hardware.cpu_matches("amd"):
        param = "nowatchdog"
        blacklist = MNT / "etc/modprobe.d/blacklist-watchdog.conf"
        blacklist.parent.mkdir(parents=True, exist_ok=True)
        blacklist.write_text("# watchdog\nblacklist sp5100_tco\n", encoding="utf-8")
    else:
        return 0

    with _target_bootloader() as bootloader:
        result = bootloader.add_kernel_params([param])
    if result.needs_mkinitcpio:
        return chroot_run(["mkinitcpio", "-P"])
    if result.regen_cmd is not None:  # GRUB: regen inside the chroot
        return chroot_run(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"])
    return 0


# RouteMetric keeps wired ahead of wireless when both links are up.
# MulticastDNS is deliberately absent: the post-install network sharing
# task runs Avahi, and resolved's own mDNS responder fights it for 5353.
WIRED_NETWORK_CONF = """[Match]
Name=en*
Name=eth*

[Link]
RequiredForOnline=routable

[Network]
DHCP=yes

[DHCPv4]
RouteMetric=100

[IPv6AcceptRA]
RouteMetric=100
"""

WIRELESS_NETWORK_CONF = """[Match]
Name=wl*

[Link]
RequiredForOnline=routable

[Network]
DHCP=yes

[DHCPv4]
RouteMetric=600

[IPv6AcceptRA]
RouteMetric=600
"""

IWD_STATE = Path("/var/lib/iwd")
IWD_PROFILE_GLOBS = ("*.psk", "*.open", "*.8021x")

SERVICE_OWNERS = (
    ("openssh", "sshd"),
    ("networkmanager", "NetworkManager"),
    ("iwd", "iwd"),
    ("dhcpcd", "dhcpcd"),
    ("bluez", "bluetooth"),
)


def _target_has(pkg: str) -> bool:
    return subprocess.run(
        ["arch-chroot", str(MNT), "pacman", "-Qq", pkg], capture_output=True
    ).returncode == 0


def _pin_iwd_to_authentication_only() -> None:
    """Write EnableNetworkConfiguration=false into the target's iwd config.

    Letting iwd and systemd-networkd both configure the link is the
    conflict core/iwd.py exists to undo: resolved refuses DNS changes
    from a second party once the link is managed, and every association
    logs LinkBusy while Wi-Fi keeps half-working. installarch shipped
    exactly that combination (EnableNetworkConfiguration=true *and*
    networkd .network files); the corrected split goes in from the start
    here, so the post-install task has nothing left to fix.
    """
    from ..core import iwd

    conf = MNT / "etc/iwd/main.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    text = conf.read_text(encoding="utf-8") if conf.is_file() else ""
    conf.write_text(
        iwd.set_option(text, iwd.SECTION, iwd.KEY, iwd.VALUE), encoding="utf-8"
    )
    print(f"{conf} <- {iwd.KEY} = {iwd.VALUE}")


def enable_services() -> int:
    """Enable services for installed packages, TRIM + DHCP via systemd-networkd.

    Wireless needs as much care here as wired. iwd associates but does
    not address the link unless EnableNetworkConfiguration is on, and
    its default is off — so enabling iwd and writing a .network file for
    `en*` only left a laptop authenticated with no IP address at all.
    systemd-networkd needs no extra package, so it is offered whenever
    NetworkManager is absent.
    """
    if not target_ready():
        return 1

    rc = 0
    for pkg, service in SERVICE_OWNERS:
        if _target_has(pkg):
            rc |= run(["systemctl", "--root", str(MNT), "enable", service])

    # Not in SERVICE_OWNERS: fstrim.timer comes from util-linux, which is in
    # base, so there is always a unit here and the package gate would be a
    # tautology. Nor is it gated on a disk that supports discard -- the unit
    # util-linux ships already runs `fstrim --quiet-unsupported`, so on a
    # machine with none it is a weekly no-op that prints nothing, and a gate
    # here would be archsetup deciding again what the tool already decides.
    # Why any of this, and why the timer rather than `discard` in fstab:
    # core/trim.py.
    rc |= run(["systemctl", "--root", str(MNT), "enable", "fstrim.timer"])

    if not _target_has("networkmanager") and ask_yes(t("inst.networkd_q")):
        network_dir = MNT / "etc/systemd/network"
        network_dir.mkdir(parents=True, exist_ok=True)
        (network_dir / "20-wired.network").write_text(
            WIRED_NETWORK_CONF, encoding="utf-8"
        )
        print(f"{network_dir / '20-wired.network'} <- DHCP (en*, eth*)")

        if _target_has("iwd"):
            (network_dir / "20-wireless.network").write_text(
                WIRELESS_NETWORK_CONF, encoding="utf-8"
            )
            print(f"{network_dir / '20-wireless.network'} <- DHCP (wl*)")
            _pin_iwd_to_authentication_only()

        rc |= run(["systemctl", "--root", str(MNT), "enable",
                   "systemd-networkd", "systemd-resolved"])
        resolv = MNT / "etc/resolv.conf"
        resolv.unlink(missing_ok=True)
        resolv.symlink_to("../run/systemd/resolve/stub-resolv.conf")
    return rc


def copy_network_config() -> int:
    """Carry the live environment's saved Wi-Fi networks into the target.

    Connecting once with iwctl in the ISO is then enough: the installed
    system boots onto the same network instead of needing a cable or a
    second `iwctl station connect`. The profile files hold the network
    passphrase, so they are copied 0600 into a 0700 directory and their
    contents are never printed — only the network names.
    """
    if not target_ready():
        return 1
    if not _target_has("iwd"):
        print(t("inst.net_no_iwd"))
        return 1

    profiles = sorted(
        path for pattern in IWD_PROFILE_GLOBS for path in IWD_STATE.glob(pattern)
    )
    if not profiles:
        print(t("inst.net_no_profiles", path=IWD_STATE))
        return 0

    print(t("inst.net_found"))
    for path in profiles:
        print(f"  {path.stem}")
    if not ask_yes(t("inst.net_copy_q")):
        print(t("msg.cancelled"))
        return 0

    target = MNT / "var/lib/iwd"
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o700)
    for path in profiles:
        destination = target / path.name
        destination.write_bytes(path.read_bytes())
        destination.chmod(0o600)  # holds the network passphrase
    print(t("inst.net_copied", count=len(profiles), path=target))
    return 0


SETUP_MODE_VAR = Path(
    "/sys/firmware/efi/efivars/SetupMode-8be4df61-93ca-11d2-aa0d-00e098032b8c"
)
SDBOOT_SRC = "/usr/lib/systemd/boot/efi/systemd-bootx64.efi"
# What the firmware actually loads. bootctl put unsigned copies here.
ESP_BINARIES = ("EFI/systemd/systemd-bootx64.efi", "EFI/BOOT/BOOTX64.EFI")


def setup_mode() -> bool | None:
    """Firmware Secure Boot setup mode; None when it cannot be read.

    Read straight from efivarfs — a 4-byte attribute header followed by
    the value — so no efivar/efitools binary is required.
    """
    try:
        raw = SETUP_MODE_VAR.read_bytes()
    except OSError:
        return None
    return bool(raw[4]) if len(raw) > 4 else None


def setup_secure_boot() -> int:
    """sbctl: create/enroll keys, sign systemd-boot and every UKI."""
    if not target_ready():
        return 1
    if not _target_has("sbctl"):
        print(t("inst.sbctl_missing"))
        return 1

    mode = setup_mode()
    if mode is False:
        # enroll-keys cannot write PK/KEK outside setup mode; going on
        # would sign files against keys the firmware will never trust.
        print(t("inst.sb_not_setup_mode"))
        return 1
    if mode is None:
        print(t("inst.sb_mode_unknown"))
        if not ask_yes(t("inst.sb_continue_q")):
            return 1

    rc = chroot_run(["sbctl", "create-keys"])
    rc |= chroot_run(["sbctl", "enroll-keys", "-m"])
    if rc != 0:
        print(t("inst.sb_enroll_failed"))
        return rc

    # Two different files, both needed. The one under /usr/lib is what
    # systemd-boot-update.service copies to the ESP after a systemd
    # upgrade, so it has to exist in signed form...
    rc |= chroot_run(["sbctl", "sign", "-s", "-o", f"{SDBOOT_SRC}.signed", SDBOOT_SRC])
    # ...but signing it does nothing for *this* boot: `bootctl install`
    # already copied the unsigned binary onto the ESP. Leaving those
    # unsigned is why an install can report "signed" and still be
    # rejected by Secure Boot on the next boot.
    for relative in ESP_BINARIES:
        if (ESP / relative).is_file():
            rc |= chroot_run(["sbctl", "sign", "-s", f"/efi/{relative}"])

    signed_uki = False
    for preset in _presets():  # every kernel, not just the first alphabetically
        uki = default_uki_path(preset)
        if uki and (MNT / uki.lstrip("/")).is_file():
            # No -s here, unlike the boot binaries above. sbctl's database is
            # a permanent list and a UKI is not permanent: it dies with its
            # kernel, and on an mkinitcpio-preset machine nothing takes the
            # entry back out -- sbctl's own remove-file runs from
            # kernel-install.d, which this layout never calls. The leftover
            # entry then fails `sbctl sign-all`, which sbctl ships as a pacman
            # PostTransaction hook, so *every* later transaction ends in an
            # error. Measured 2026-08-17: removing linux-g14 left its UKI in
            # files.json and zz-sbctl.hook reported "failed signing ... does
            # not exist" from then on. The entry buys nothing either -- the
            # package's mkinitcpio post hook re-signs the UKI by path on
            # every rebuild.
            rc |= chroot_run(["sbctl", "sign", uki])
            signed_uki = True
    if not signed_uki:
        print(t("inst.sb_no_uki"))

    if _target_has("edk2-shell"):
        shell = ESP / "shellx64.efi"
        if not shell.is_file():
            shell.write_bytes(
                (MNT / "usr/share/edk2-shell/x64/Shell.efi").read_bytes()
            )
        rc |= chroot_run(["sbctl", "sign", "-s", "/efi/shellx64.efi"])

    # No re-signing hook of our own. sbctl ships two already: a mkinitcpio
    # post hook that signs the image it was just handed ($3, the UKI), and
    # the pacman hook above. What used to live here -- /etc/initcpio/post/
    # uki-sbctl running `sbctl sign-all` -- walks the *database*, so it never
    # touched the UKI mkinitcpio had just built. Measured on a live machine
    # (pacman.log 2026-08-15): on every rebuild it reported the four boot
    # binaries as "already signed" and the package's hook did the real work.

    print(f"\n{t('inst.sb_verify')}")
    chroot_run(["sbctl", "verify"])  # report only: unrelated ESP files may fail
    return rc
