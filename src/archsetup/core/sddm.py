"""SDDM theme installation (Silent).

Sugar Candy used to be the second option here. It was dropped in 2026-07:
the theme was never installed on either machine, and the tarball it was
unpacked from was deleted from the dotfiles repo, so the task could only
ever fail at its own file check.
"""

from __future__ import annotations

import getpass
import shutil
import subprocess
from pathlib import Path

from . import dotfiles, i18n, pacman
from .pacman import run
from .prompt import ask_yes
from .sysedit import sudo_install, sudo_mkdir, sudo_write

t = i18n.t

SDDM_CONF = Path("/etc/sddm.conf")
THEME_DIR = Path("/usr/share/sddm/themes/silent")
CHANGE_AVATAR = THEME_DIR / "change_avatar.sh"
FACES_DIR = Path("/usr/share/sddm/faces")

# Resimler dizinine gore; XDG adi makineye gore degisiyor (Pictures/Resimler).
AVATAR_RELATIVE = Path("Icons") / "Death_Star.png"

# The greeter user's name is fixed in the sddm binary -- sddm.conf.5 has no key
# for it (User= belongs to autologin) -- so it is a constant here. Its *home*
# is a packaging choice and is read from passwd, see _greeter_home().
GREETER_USER = "sddm"

# One theme name, two consumers, and they have to agree or the greeter shows
# two different cursors: sddm exports CursorTheme= as XCURSOR_THEME for the
# root arrow (libXcursor, honours the env var), while the theme's own
# cursorShape items go through libxcb-cursor, which never reads it -- that
# half is what CURSOR_CAP below answers.
CURSOR_THEME = "Mocu-White-Right"

# Kept byte-identical to dotfiles' sddm/sddm.conf, which is what the running
# machines have. Written here rather than read from there so the task works
# before the dotfiles repo is cloned.
SILENT_CONF = f"""[General]
InputMethod=qtvirtualkeyboard
GreeterEnvironment=QML2_IMPORT_PATH=/usr/share/sddm/themes/silent/components/,QT_IM_MODULE=qtvirtualkeyboard
Numlock=on

[Theme]
CursorTheme={CURSOR_THEME}
Current=silent
"""

# First entry of libxcb-cursor's built-in search path, with ~ expanded from
# HOME. Relative on purpose: the absolute target is built from passwd.
CURSOR_CAP_RELATIVE = Path(".local/share/icons/default/index.theme")

# Mirrors dotfiles' sddm/greeter-default-index.theme. The two lines that differ
# are the two that spell an absolute path (that copy's target label, and the
# measured HOME): here both are discovered rather than assumed.
CURSOR_CAP = f"""# Cursor fallback for the SDDM greeter, which runs as user {GREETER_USER}
# (HOME from passwd; the greeter's own .cache/sddm-greeter-qt6/ shows it uses it).
#
# Why this file and not XCURSOR_THEME: the greeter is Qt6/xcb, and Qt6 resolves
# cursors through libxcb-cursor. That library reads the theme NAME only from the
# "Xcursor.theme" X resource -- the literal "XCURSOR_THEME" does not appear in
# libxcb-cursor.so.0 at all, so the env var sddm exports from
# [Theme] CursorTheme= is ignored on this path. With no X resource manager on
# the greeter's display (Xsetup is empty and xrdb is not installed) the library
# falls back to the theme named "default" and searches
#   ~/.local/share/icons:~/.icons:/usr/share/icons:/usr/share/pixmaps
# with ~ expanded from HOME. This file is therefore the first hit, and it wins
# over /usr/share/icons/default/index.theme, which says Inherits=Adwaita.
#
# The other cursor path (the plain arrow, inherited from the X root window) is
# handled by sddm itself: it runs `xsetroot -cursor_name left_ptr` with
# XCURSOR_THEME set, and xsetroot uses libXcursor, which DOES honour that env
# var. That path needs the xorg-xsetroot package -- without it sddm logs
# "Could not setup default cursor" and the arrow stays the X server built-in.
#
# Comment syntax matters: '/* ... */' parses fine for libXcursor's line scanner
# but makes GLib reject the whole file ("not a key-value pair, group, or
# comment"). Keep comments '#'.
[Icon Theme]
Name=Default
Comment=Default Cursor Theme
Inherits={CURSOR_THEME}
"""


def _sddm_installed() -> bool:
    return shutil.which("sddm") is not None


def _greeter_home() -> Path | None:
    """The greeter user's home directory, from passwd rather than assumed.

    /var/lib/sddm where this was measured, but that is a packaging choice and
    not a fact about sddm; the directory CURSOR_CAP has to land in is expanded
    from this HOME, so a guess writes a file nothing ever reads.

    Parsed by hand instead of through pacman.query(): that helper splits on
    whitespace, and the GECOS field of this very entry contains spaces
    ("SDDM Greeter Account"), which would fold three colon-separated fields
    into one token.
    """
    out = subprocess.run(
        ["getent", "passwd", GREETER_USER], capture_output=True, text=True
    )
    if out.returncode != 0:
        return None
    fields = out.stdout.rstrip("\n").split(":")
    if len(fields) < 6 or not fields[5]:
        return None
    return Path(fields[5])


def install_cursor_cap() -> int:
    """Hand the greeter a cursor theme name on the one path it reads.

    CursorTheme= in sddm.conf is not enough on its own and never was: it
    reaches the root arrow through XCURSOR_THEME, and the theme's own
    cursorShape items through libxcb-cursor, which does not read that
    variable at all. The name has to be on disk, in a "default" theme that
    comes before /usr/share/icons/default (Inherits=Adwaita) in the search
    path -- so, in the greeter's own home.

    No prompt: the file belongs to the config the caller just asked about,
    and the answer to a second question would be the same answer.
    """
    home = _greeter_home()
    if home is None or not home.is_dir():
        print(t("sddm.greeter_home_missing", user=GREETER_USER))
        return 0

    target = home / CURSOR_CAP_RELATIVE
    # 700 and greeter-owned all the way down: the only reader is the greeter,
    # and this chain is inside a home directory that is not ours to widen.
    current = home
    for part in CURSOR_CAP_RELATIVE.parent.parts:
        current = current / part
        rc = sudo_mkdir(current, GREETER_USER, "700")
        if rc != 0:
            return rc

    rc = sudo_install(target, CURSOR_CAP, GREETER_USER, "644")
    if rc == 0:
        print(t("sddm.cursor_done", path=target, theme=CURSOR_THEME))
    return rc


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
    # xorg-xsetroot is an undeclared dependency of the CursorTheme= line this
    # task writes: sddm starts `xsetroot -cursor_name left_ptr` with
    # XCURSOR_THEME set, and Arch's sddm package does not pull it in. Without
    # it the journal says "Could not setup default cursor" and the arrow stays
    # the X server built-in -- the config looks right and the screen is wrong.
    # It goes in the task rather than the catalog because the task is what
    # writes the line that needs it.
    rc = pacman.install(["xorg-xsetroot"], ["sddm-silent-theme"])
    if rc != 0:
        return rc
    if SDDM_CONF.exists() and not ask_yes(t("sddm.overwrite_q", path=SDDM_CONF)):
        print(t("msg.cancelled"))
        return 0
    rc = sudo_write(SDDM_CONF, SILENT_CONF)
    if rc != 0:
        return rc
    # After the config, and skipped with it when the user declines: the file
    # names the same theme, and placing it next to someone else's sddm.conf
    # would make the greeter's two cursor paths disagree.
    rc = install_cursor_cap()
    if rc != 0:
        return rc
    print(t("sddm.done"))
    return install_avatar()
