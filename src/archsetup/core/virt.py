"""libvirt / virt-manager configuration.

Ported from installarchde's config_virt_manager with fixes:
- mkinitcpio -P actually runs after adding the virtio modules (the old
  script edited MODULES but never regenerated the initramfs),
- libvirtd is enabled and started before virsh net-autostart/net-start
  (the old order ran virsh against a stopped daemon).
"""

from __future__ import annotations

import getpass
import subprocess
from pathlib import Path

from . import gpuconfig, i18n, pacman, services
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

LIBVIRTD_CONF = Path("/etc/libvirt/libvirtd.conf")
QEMU_CONF = Path("/etc/libvirt/qemu.conf")
NETWORK_CONF = Path("/etc/libvirt/network.conf")
VIRT_MODULES = ("virtio", "virtio_blk", "virtio_pci", "virtio_net")


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

    if gpuconfig._merge_modules(VIRT_MODULES):
        run(["sudo", "mkinitcpio", "-P"])

    for group in ("libvirt", "kvm"):
        if not _group_exists(group):
            run(["sudo", "groupadd", "-f", group])

    rc = run(["sudo", "usermod", "-aG", "kvm,libvirt", user])
    rc |= services.enable("libvirtd.service")
    rc |= services.enable("virtlogd.service")
    rc |= run(["sudo", "systemctl", "start", "libvirtd.service"])

    # Tolerated: these fail harmlessly when the network is already active.
    run(["sudo", "virsh", "net-autostart", "default"])
    run(["sudo", "virsh", "net-start", "default"])

    if rc == 0:
        print(t("virt.done"))
    return rc
