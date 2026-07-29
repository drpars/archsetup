"""Network sharing (Samba + Avahi) and network-online boot behaviour.

Ported from installarchde's network_config with fixes: the sambashare
group is created before the usershares chown (the old order failed on
systems without the group), and the smb.conf log line uses %m directly
(the old printf needed %% escaping).
"""

from __future__ import annotations

import getpass
import shutil
import subprocess
from pathlib import Path

from . import i18n, pacman, services
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

SMB_CONF = Path("/etc/samba/smb.conf")
USERSHARES = "/var/lib/samba/usershares"

WAIT_ONLINE_UNIT = "systemd-networkd-wait-online.service"
WAIT_ONLINE_DROPIN = Path(
    "/etc/systemd/system/systemd-networkd-wait-online.service.d/any.conf"
)
WAIT_ONLINE_DROPIN_CONTENT = """\
# Continue as soon as *any* interface is routable.
# The default waits for all of them, which costs 120 s of boot whenever an
# ethernet port sits there with no cable in it.
#
# timeout=3: on wifi this service times out every boot anyway — iwd itself
# needs several seconds to start, and association plus DHCP does not fit in
# any sane limit. Waiting longer buys nothing: network-online.target is
# reached either way and the units ordered after it start either way. So the
# timeout is kept as short as is useful rather than as long as is hopeful.
[Service]
ExecStart=
ExecStart=/usr/lib/systemd/systemd-networkd-wait-online --any --timeout=3
"""

SMB_CONF_CONTENT = """[global]
workgroup = WORKGROUP
usershare path = /var/lib/samba/usershares
usershare max shares = 100
usershare allow guests = no
server string = Samba Server
client min protocol = SMB3
server min protocol = SMB3
server role = standalone server
log file = /var/log/samba/%m.log
max log size = 1000
vfs objects = fruit streams_xattr
fruit:metadata = stream
fruit:model = Macintosh
"""


def _group_exists(name: str) -> bool:
    return (
        subprocess.run(["getent", "group", name], capture_output=True).returncode == 0
    )


def _configure_samba() -> int:
    if SMB_CONF.is_file():
        run(["sudo", "cp", str(SMB_CONF), f"{SMB_CONF}.bak"])
    rc = run(["sudo", "mkdir", "-p", str(SMB_CONF.parent)])
    rc |= sudo_write(SMB_CONF, SMB_CONF_CONTENT)

    if not _group_exists("sambashare"):
        rc |= run(["sudo", "groupadd", "-r", "sambashare"])
    user = getpass.getuser()
    rc |= run(["sudo", "gpasswd", "-a", user, "sambashare"])

    rc |= run(["sudo", "mkdir", "-p", USERSHARES])
    rc |= run(["sudo", "chown", "root:sambashare", USERSHARES])
    rc |= run(["sudo", "chmod", "1770", USERSHARES])

    rc |= run(["sudo", "systemctl", "restart", "smb.service", "nmb.service"])
    print(t("network.smbpasswd", user=user))
    rc |= run(["sudo", "smbpasswd", "-a", user])
    rc |= services.enable("smb")
    rc |= services.enable("nmb")
    return rc


def wait_online_timeout() -> int:
    """Stop systemd-networkd-wait-online from blocking boot for two minutes.

    The service is deliberately *not* disabled: smb, nmb and
    archlinux-keyring-wkd-sync.service are ordered after
    network-online.target, and disabling the only unit that reaches that
    target would leave them waiting on something that never arrives.
    """
    if not services.unit_exists(WAIT_ONLINE_UNIT):
        print(t("network.no_wait_online"))
        return 0

    rc = run(["sudo", "mkdir", "-p", str(WAIT_ONLINE_DROPIN.parent)])
    rc |= sudo_write(WAIT_ONLINE_DROPIN, WAIT_ONLINE_DROPIN_CONTENT)
    rc |= run(["sudo", "systemctl", "daemon-reload"])
    if rc == 0:
        print(t("network.wait_online_done", path=WAIT_ONLINE_DROPIN))
    return rc


def configure() -> int:
    rc = pacman.install(["samba", "avahi"], [])
    if rc != 0:
        return rc

    if pacman.is_installed("samba"):
        rc |= _configure_samba()
    if pacman.is_installed("avahi"):
        rc |= services.enable("avahi-daemon.service")

    if shutil.which("firewall-cmd"):
        rc |= run(["sudo", "firewall-cmd", "--permanent", "--add-service=samba"])
        rc |= run(["sudo", "firewall-cmd", "--reload"])

    if rc == 0:
        print(t("network.done"))
    return rc
