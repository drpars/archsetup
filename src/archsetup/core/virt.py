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
