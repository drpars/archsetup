"""SDDM theme installation (Silent, Sugar Candy)."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import i18n, pacman
from .dotfiles import DOTFILES_DIR, ensure_dotfiles_repo
from .pacman import run
from .prompt import ask_yes
from .sysedit import sudo_write

t = i18n.t

SDDM_CONF = Path("/etc/sddm.conf")
SDDM_CONF_DIR = Path("/etc/sddm.conf.d")
THEMES_DIR = Path("/usr/share/sddm/themes")

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


def install_sugarcandy() -> int:
    if not _sddm_installed():
        print(t("msg.sddm_missing"))
        return 1
    if not DOTFILES_DIR.is_dir():
        if ensure_dotfiles_repo() != 0:
            return 1

    source_conf = DOTFILES_DIR / "sddm" / "sddm.conf"
    tarball = DOTFILES_DIR / "sddm" / "sugar-candy" / "sugar-candy.tar.gz"
    if not source_conf.is_file() or not tarball.is_file():
        print(t("sddm.files_missing", path=DOTFILES_DIR / "sddm"))
        return 1

    rc = run(["sudo", "mkdir", "-p", str(SDDM_CONF_DIR), str(THEMES_DIR)])
    rc |= sudo_write(SDDM_CONF_DIR / "10-theme.conf", "[Theme]\nCurrent=sugar-candy\n")
    rc |= sudo_write(
        SDDM_CONF_DIR / "sddm.conf", source_conf.read_text(encoding="utf-8")
    )
    rc |= run(["sudo", "tar", "-xzf", str(tarball), "-C", str(THEMES_DIR)])
    if rc == 0:
        print(t("sddm.done"))
    return rc
