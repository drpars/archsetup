"""Samba + Avahi network sharing configuration.

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
