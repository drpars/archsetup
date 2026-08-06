"""libvirt / virt-manager configuration.

Ported from installarchde's config_virt_manager with fixes:
- no virtio modules are added to the initramfs. virtio_blk/pci/net are VM
  *guest* drivers and do nothing for a virt-manager *host*; the host side
  is kvm_amd/kvm_intel, which ship built in. The old script added them and
  every kernel that does not build them (linux-g14) then failed at boot
  with "Failed to find module 'virtio_blk'",
- libvirtd runs through socket activation instead of being enabled at boot
  (see _enable_libvirt_sockets).
"""

from __future__ import annotations

import getpass
import subprocess
from pathlib import Path

from . import i18n, pacman, services
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

LIBVIRTD_CONF = Path("/etc/libvirt/libvirtd.conf")
QEMU_CONF = Path("/etc/libvirt/qemu.conf")
NETWORK_CONF = Path("/etc/libvirt/network.conf")

# Enabling libvirtd.service costs ~2.5 s on the critical boot path
# (multi-user.target <- libvirtd <- virtlogd) and Arch's own preset ships it
# disabled. Socket activation starts the daemon on first access instead.
LIBVIRT_SOCKETS = (
    "libvirtd.socket",
    "libvirtd-ro.socket",
    "libvirtd-admin.socket",
    "virtlogd.socket",
    "virtlockd.socket",
)

VFIOCTL_URL = "https://github.com/drpars/vfioctl.git"


def install_vfioctl() -> int:
    """Clone vfioctl and build it with makepkg, the way yay-bin is bootstrapped.

    Why a task and not a catalogue entry. This file's own rule says a package
    with nothing left to do after pacman is a catalogue entry -- but that rule
    assumes the catalogue can reach it. vfioctl is deliberately not published
    to the AUR, so a [[category.packages]] line would be a name no repository
    resolves, and by the trap recorded for this file that drops the *entire*
    category transaction, not just the one entry. The catalogue path does not
    exist here, so what remains is a task.

    Why archsetup installs a tool it does not own. Installing is not owning:
    yay-bin arrives through this same call and archsetup owns none of it. What
    decides ownership is who writes the configuration, and every file vfioctl
    configures stays vfioctl's -- the handover hook, the kvmfr permissions,
    libvirt's device ACL. What archsetup ends is the odd seam where every other
    link in the install chain was a package and the last one was "go and clone
    this yourself".

    Rebuilding is the update path, and it is the only one. vfioctl is not in
    any repository, so `pacman -Syu` never touches it; running this task again
    re-clones and rebuilds, which is exactly what upgrading it means. Whoever
    does not run archsetup again stays on the version they installed, and the
    message below says so rather than leaving it to be discovered.
    """
    # base-devel is a meta package (not a group) since 2022, so a single -Qq
    # answers it. makepkg itself ships in `pacman` and is always present --
    # checking for it would test nothing.
    if not pacman.is_installed("base-devel"):
        print(t("virt.vfioctl_needs_base_devel"))
        return 1

    rc = pacman.install_from_aur_git(VFIOCTL_URL)
    if rc == 0:
        print(t("virt.vfioctl_done"))
    return rc


def _append_once(path: Path, marker: str, block: str) -> bool:
    """Append block unless marker already present; True if file changed."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        print(t("msg.file_missing", path=path))
        return False
    if marker in text:
        print(t("virt.already", path=path))
        return False
    return sudo_write(path, f"{text.rstrip()}\n\n{block}") == 0


def _group_exists(name: str) -> bool:
    return (
        subprocess.run(["getent", "group", name], capture_output=True).returncode == 0
    )


def _enable_libvirt_sockets() -> int:
    """Switch libvirt to socket activation.

    Order matters. libvirtd.service carries Also= lines for virtlockd.socket,
    virtlogd.socket, libvirtd.socket, libvirtd-ro.socket and
    libvirtd-admin.socket, so `systemctl disable libvirtd.service` takes every
    one of those sockets down with it. Disabling after enabling would leave the
    machine with neither the service nor socket activation, and virt-manager
    could no longer start the daemon at all.

    The sockets are started as well as enabled: the virsh net-* calls below
    connect immediately, and an enabled-but-not-started socket would refuse
    them until the next boot.
    """
    rc = services.disable("libvirtd.service")
    rc |= run(["sudo", "systemctl", "enable", "--now", *LIBVIRT_SOCKETS])
    return rc


def configure() -> int:
    if not pacman.is_installed("libvirt"):
        print(t("virt.libvirt_missing"))
        return 1

    user = getpass.getuser()
    if LIBVIRTD_CONF.is_file():
        run(["sudo", "cp", str(LIBVIRTD_CONF), f"{LIBVIRTD_CONF}.bak"])

    _append_once(
        LIBVIRTD_CONF,
        "unix_sock_group = 'libvirt'",
        "unix_sock_group = 'libvirt'\nunix_sock_rw_perms = '0770'\n",
    )
    _append_once(
        QEMU_CONF,
        f'user = "{user}"',
        f'user = "{user}"\ngroup = "{user}"\n',
    )
    _append_once(
        NETWORK_CONF,
        'firewall_backend="iptables"',
        'firewall_backend="iptables"\n',
    )

    for group in ("libvirt", "kvm"):
        if not _group_exists(group):
            run(["sudo", "groupadd", "-f", group])

    rc = run(["sudo", "usermod", "-aG", "kvm,libvirt", user])
    rc |= _enable_libvirt_sockets()

    # Tolerated: these fail harmlessly when the network is already active.
    run(["sudo", "virsh", "net-autostart", "default"])
    run(["sudo", "virsh", "net-start", "default"])

    if rc == 0:
        print(t("virt.done"))
    return rc
