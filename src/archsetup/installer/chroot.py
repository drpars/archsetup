"""Target system configuration via arch-chroot.

Replaces the old script's copy-itself-into-/mnt trick: every step is a
direct file edit under /mnt or an `arch-chroot /mnt <command>` call.
User passwords are set interactively with passwd inside the chroot, so
they never pass through shell pipes or variables.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ..core import hardware, i18n
from ..core.pacman import run
from ..core.prompt import ask_yes

t = i18n.t

MNT = Path("/mnt")
DEFAULT_KEYMAP = "trq"
DEFAULT_LOCALE = "tr_TR"
DEFAULT_TIMEZONE = "Europe/Istanbul"


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
        lines.append("FONT=ter-v16b")
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


def create_swapfile() -> int:
    if not target_ready():
        return 1
    raw = input(f"{t('inst.swapsize_q')} [8192]: ").strip() or "8192"
    if not raw.isdigit() or int(raw) < 1:
        print(t("inst.invalid"))
        return 1
    rc = run(["dd", "if=/dev/zero", f"of={MNT}/swapfile", "bs=1M",
              f"count={raw}", "status=progress"])
    rc |= run(["chmod", "600", f"{MNT}/swapfile"])
    rc |= run(["mkswap", f"{MNT}/swapfile"])
    rc |= run(["swapon", f"{MNT}/swapfile"])
    return rc


def _presets() -> list[Path]:
    return sorted((MNT / "etc/mkinitcpio.d").glob("*.preset"))


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
    match = re.search(
        r'^default_uki="?([^"\n]+)"?', preset.read_text(encoding="utf-8"), re.MULTILINE
    )
    return match.group(1).strip() if match else None


def gen_uki() -> int:
    """Switch the target to systemd initramfs + Unified Kernel Image output."""
    if not target_ready():
        return 1
    preset = _choose_preset()
    if preset is None:
        return 1

    mkconf = MNT / "etc/mkinitcpio.conf"
    mkconf.write_text(
        mkconf.read_text(encoding="utf-8").replace("base udev", "base systemd", 1),
        encoding="utf-8",
    )

    text = preset.read_text(encoding="utf-8")
    text = re.sub(r"^#(ALL_config)", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"^#(default_uki)", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"^#(default_options)", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"^(default_image=)", r"#\1", text, flags=re.MULTILINE)
    text = re.sub(
        r"^PRESETS=\('default' 'fallback'\)", "PRESETS=('default')", text,
        flags=re.MULTILINE,
    )
    preset.write_text(text, encoding="utf-8")

    uki = default_uki_path(preset)
    if uki is None:
        print(t("inst.no_uki_line", preset=preset))
        return 1
    rc = chroot_run(["mkdir", "-p", str(Path(uki).parent)])
    rc |= chroot_run(["mkinitcpio", "-P"])
    return rc


def disable_watchdog() -> int:
    """Blacklist watchdog modules and add the matching kernel parameter."""
    cmdline = MNT / "etc/kernel/cmdline"
    if not cmdline.is_file():
        print(t("inst.no_cmdline"))
        return 1
    tokens = cmdline.read_text(encoding="utf-8").split()

    if hardware.cpu_matches("intel"):
        param = "modprobe.blacklist=iTCO_wdt"
    elif hardware.cpu_matches("amd"):
        param = "nowatchdog"
        blacklist = MNT / "etc/modprobe.d/blacklist-watchdog.conf"
        blacklist.parent.mkdir(parents=True, exist_ok=True)
        blacklist.write_text("# watchdog\nblacklist sp5100_tco\n", encoding="utf-8")
    else:
        return 0

    if param in tokens:
        print(t("msg.param_present", param=param))
        return 0
    tokens.append(param)
    cmdline.write_text(" ".join(tokens) + "\n", encoding="utf-8")
    print(f"/mnt/etc/kernel/cmdline <- {param}")
    return 0


WIRED_NETWORK_CONF = """[Match]
Name=en*

[Network]
DHCP=yes
"""

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


def enable_services() -> int:
    """Enable services for installed packages + wired DHCP via networkd.

    iwd only manages wireless interfaces; without this step a wired
    machine (or a QEMU guest with a virtio NIC) boots with no network.
    systemd-networkd needs no extra package, so it is offered whenever
    NetworkManager is absent.
    """
    if not target_ready():
        return 1

    rc = 0
    for pkg, service in SERVICE_OWNERS:
        if _target_has(pkg):
            rc |= run(["systemctl", "--root", str(MNT), "enable", service])

    if not _target_has("networkmanager") and ask_yes(t("inst.networkd_q")):
        network_dir = MNT / "etc/systemd/network"
        network_dir.mkdir(parents=True, exist_ok=True)
        (network_dir / "20-wired.network").write_text(
            WIRED_NETWORK_CONF, encoding="utf-8"
        )
        rc |= run(["systemctl", "--root", str(MNT), "enable",
                   "systemd-networkd", "systemd-resolved"])
        resolv = MNT / "etc/resolv.conf"
        resolv.unlink(missing_ok=True)
        resolv.symlink_to("../run/systemd/resolve/stub-resolv.conf")
        print(f"{network_dir / '20-wired.network'} <- DHCP (en*)")
    return rc


def setup_secure_boot() -> int:
    """sbctl: create/enroll keys, sign systemd-boot and the UKI."""
    if not target_ready():
        return 1
    if subprocess.run(
        ["arch-chroot", str(MNT), "pacman", "-Qq", "sbctl"], capture_output=True
    ).returncode != 0:
        print(t("inst.sbctl_missing"))
        return 1

    rc = chroot_run(["sbctl", "create-keys"])
    rc |= chroot_run(["sbctl", "enroll-keys", "-m"])
    rc |= chroot_run([
        "sbctl", "sign", "-s",
        "-o", "/usr/lib/systemd/boot/efi/systemd-bootx64.efi.signed",
        "/usr/lib/systemd/boot/efi/systemd-bootx64.efi",
    ])
    presets = _presets()
    if presets and (uki := default_uki_path(presets[0])):
        rc |= chroot_run(["sbctl", "sign", "-s", uki])

    hook = MNT / "etc/initcpio/post/uki-sbctl"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env bash\nsbctl sign-all\n", encoding="utf-8")
    hook.chmod(0o755)
    return rc
