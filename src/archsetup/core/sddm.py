"""SDDM theme installation (Silent).

Sugar Candy used to be the second option here. It was dropped in 2026-07:
the theme was never installed on either machine, and the tarball it was
unpacked from was deleted from the dotfiles repo, so the task could only
ever fail at its own file check.
"""

from __future__ import annotations

import getpass
import shutil
from pathlib import Path

from . import dotfiles, i18n, pacman
from .pacman import run
from .prompt import ask_yes
from .sysedit import sudo_write

t = i18n.t

SDDM_CONF = Path("/etc/sddm.conf")
THEME_DIR = Path("/usr/share/sddm/themes/silent")
CHANGE_AVATAR = THEME_DIR / "change_avatar.sh"
FACES_DIR = Path("/usr/share/sddm/faces")

# Resimler dizinine gore; XDG adi makineye gore degisiyor (Pictures/Resimler).
AVATAR_RELATIVE = Path("Icons") / "Death_Star.png"

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


def _avatar_source() -> Path | None:
    path = dotfiles._xdg_dir("PICTURES", "Pictures") / AVATAR_RELATIVE
    return path if path.is_file() else None


def install_avatar() -> int:
    """Giris ekrani avatarini yerlestir.

    Isi temanin kendi change_avatar.sh'ina birakiyoruz: kirpma ve 256x256
    olcekleme onun bildigi sey, ve tema guncellenirse birlikte guncellenir.
    Bizim yaptigimiz kaynak dosyayi bulmak ve mogrify'in kurulu oldugundan
    emin olmak.

    Avatar bugune kadar hic kurulmamisti: dotfiles'taki user_icon_symlink
    dosyasi symlink degil, icinde yol yazan duz metindi (git modu 100755),
    yani hicbir sey onu takip edemezdi.
    """
    user = getpass.getuser()
    target = FACES_DIR / f"{user}.face.icon"

    source = _avatar_source()
    if source is None:
        print(t("sddm.avatar_source_missing", path=AVATAR_RELATIVE))
        return 0
    if not CHANGE_AVATAR.is_file():
        print(t("sddm.avatar_script_missing", path=CHANGE_AVATAR))
        return 0
    if not ask_yes(t("sddm.avatar_q", source=source, target=target)):
        return 0

    if shutil.which("mogrify") is None:
        # change_avatar.sh mogrify'i kosulsuz cagiriyor; yoksa sessizce
        # kirpilmamis bir dosya birakmiyor, hic birakmiyor.
        if pacman.install(["imagemagick"], []) != 0:
            return 1

    rc = run(["bash", str(CHANGE_AVATAR), user, str(source)])
    if rc == 0:
        print(t("sddm.avatar_done", path=target))
    return rc


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
    if rc != 0:
        return rc
    print(t("sddm.done"))
    return install_avatar()
