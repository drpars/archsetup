"""SDDM theme installation (Silent).

Sugar Candy used to be the second option here. It was dropped in 2026-07:
the theme was never installed on either machine, and the tarball it was
unpacked from was deleted from the dotfiles repo, so the task could only
ever fail at its own file check.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import i18n, pacman
from .prompt import ask_yes
from .sysedit import sudo_write

t = i18n.t

SDDM_CONF = Path("/etc/sddm.conf")

# Kept byte-identical to dotfiles' sddm/sddm.conf, which is what the running
# machines have. Written here rather than read from there so the task works
# before the dotfiles repo is cloned.
SILENT_CONF = """[General]
InputMethod=qtvirtualkeyboard
GreeterEnvironment=QML2_IMPORT_PATH=/usr/share/sddm/themes/silent/components/,QT_IM_MODULE=qtvirtualkeyboard
Numlock=on

[Theme]
CursorTheme=Mocu-White-Right
Current=silent
"""


def _sddm_installed() -> bool:
    return shutil.which("sddm") is not None


def install_silent() -> int:
    if not _sddm_installed():
        print(t("msg.sddm_missing"))
        return 1
    rc = pacman.install([], ["sddm-silent-theme"])
    if rc != 0:
        return rc
    if SDDM_CONF.exists() and not ask_yes(t("sddm.overwrite_q", path=SDDM_CONF)):
        print(t("msg.cancelled"))
        return 0
    rc = sudo_write(SDDM_CONF, SILENT_CONF)
    if rc == 0:
        print(t("sddm.done"))
    return rc
