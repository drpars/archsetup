"""kmscon virtual console: AUR install + per-TTY systemd unit swap.

kmscon replaces agetty on one VT with a KMS terminal: TrueType fonts,
full Unicode, XKB keyboard layouts and a scrollback buffer, none of which
the kernel console has. It earns its place as the console you drop to
when the compositor will not start — the one moment a readable, correctly
laid out keyboard matters most.

Two things about the package are easy to get wrong:

* The AUR package is **kmscon-git**. Plain `kmscon` was dropped; asking
  for it fails with "target not found" before anything is configured.
* kmscon does not read the system keymap. With no xkb-layout in its own
  config, libxkbcommon falls back to `us` — so a machine whose VTs are
  Turkish gets a US console the moment kmscon takes over. Setting the
  layout with `localectl set-x11-keymap` does not help: that writes the
  X11 default, which kmscon never looks at.

Option names are validated against kmscon's config parser (src/config.c);
an unknown key is not fatal but logs an error on every start. `font-dpi`,
which earlier versions of this file wrote, is one of those.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import i18n, pacman, prompt, services
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

CONFIG = Path("/etc/kmscon/kmscon.conf")
VCONSOLE = Path("/etc/vconsole.conf")

PACKAGE = "kmscon-git"

# kmscon-git's PKGBUILD forgets `check` in makedepends, so no AUR helper
# knows to pull it in and meson stops with
#   ERROR: Dependency "check" not found, tried pkgconfig
# after the sources are already fetched. Installing it from the repo first
# costs one small package and turns a failed build into a working one.
BUILD_DEPS = ["check"]

FONT = "JetBrainsMono Nerd Font Mono"
FONT_PACKAGE = "ttf-jetbrains-mono-nerd"
DEFAULT_SIZE = 16
DEFAULT_LAYOUT = "us"

# tokyonight-night, the same palette the TUI and the kitty/nvim dotfiles
# use, so the fallback console does not look like a different machine.
PALETTE = {
    "black": "21,22,30",
    "red": "247,118,142",
    "green": "158,206,106",
    "yellow": "224,175,104",
    "blue": "122,162,247",
    "magenta": "187,154,247",
    "cyan": "125,207,255",
    "light-grey": "169,177,214",
    "dark-grey": "65,72,104",
    "light-red": "255,122,147",
    "light-green": "185,242,124",
    "light-yellow": "255,158,100",
    "light-blue": "125,166,255",
    "light-magenta": "187,154,247",
    "light-cyan": "13,185,215",
    "white": "192,202,245",
    "foreground": "192,202,245",
    "background": "26,27,38",
}


def _ask_tty() -> int | None:
    try:
        raw = input(f"{t('kmscon.tty_q')} [3-6, 5]: ").strip() or "5"
    except EOFError:
        return None
    if raw.isdigit() and 3 <= int(raw) <= 6:
        return int(raw)
    print(t("kmscon.tty_invalid"))
    return None


def _ask_size() -> int:
    try:
        raw = input(f"{t('kmscon.size_q')} [{DEFAULT_SIZE}]: ").strip()
    except EOFError:
        return DEFAULT_SIZE
    return int(raw) if raw.isdigit() and 6 <= int(raw) <= 48 else DEFAULT_SIZE


def _ask_layout(default: str) -> str:
    try:
        raw = input(f"{t('kmscon.layout_q')} [{default}]: ").strip()
    except EOFError:
        return default
    return raw or default


def keyboard() -> dict[str, str]:
    """XKB layout/model/variant from /etc/vconsole.conf.

    localectl writes them there, so the console kmscon replaces and the
    graphical session already agree on this; reading it keeps kmscon from
    being the one thing on the machine with a different keyboard.
    """
    values = {}
    try:
        text = VCONSOLE.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"')
    return {
        "xkb-layout": values.get("XKBLAYOUT") or DEFAULT_LAYOUT,
        "xkb-model": values.get("XKBMODEL") or "pc105",
        "xkb-variant": values.get("XKBVARIANT", ""),
    }


def _font() -> str:
    """The Nerd Font if it is installed, otherwise whatever fontconfig has.

    A missing font is not an error kmscon reports usefully: it renders
    with a substitute and the glyphs in the prompt turn into boxes.
    """
    out = subprocess.run(
        ["fc-list", ":family"], capture_output=True, text=True
    )
    if FONT.lower() in out.stdout.lower():
        return FONT
    print(t("kmscon.font_missing", font=FONT))
    if prompt.ask_yes(t("kmscon.font_install_q", pkg=FONT_PACKAGE)):
        if pacman.install([FONT_PACKAGE], []) == 0:
            return FONT
    return "monospace"


def build_config(size: int, font: str, keys: dict[str, str]) -> str:
    lines = [
        f"font-name={font}",
        f"font-size={size}",
        "",
        # kmscon's own default is TERM=kmscon, whose terminfo exists only
        # where kmscon is installed -- every ssh session out of this
        # console would land on a host that cannot render it.
        "term=xterm-256color",
        "sb-size=10000",
        "",
    ]
    lines += [f"{key}={value}" for key, value in keys.items() if value]
    lines += ["", "palette=custom"]
    lines += [f"palette-{name}={rgb}" for name, rgb in PALETTE.items()]
    return "\n".join(lines) + "\n"


def install() -> int:
    tty = _ask_tty()
    if tty is None:
        print(t("msg.cancelled"))
        return 1

    rc = pacman.install(BUILD_DEPS, [PACKAGE])
    if rc != 0:
        return rc

    keys = keyboard()
    print(t("kmscon.keyboard", layout=keys["xkb-layout"], model=keys["xkb-model"]))

    chosen = _ask_layout(keys["xkb-layout"])
    if chosen != keys["xkb-layout"]:
        # A variant belongs to the layout it was written for; carrying
        # "nodeadkeys" over to another layout can fail to compile, and
        # kmscon then silently reverts to the default system keymap.
        keys["xkb-layout"], keys["xkb-variant"] = chosen, ""

    content = build_config(_ask_size(), _font(), keys)

    rc = run(["sudo", "mkdir", "-p", str(CONFIG.parent)])
    if CONFIG.is_file():
        rc |= run(["sudo", "cp", str(CONFIG), f"{CONFIG}.bak"])
    rc |= sudo_write(CONFIG, content)

    # Failure to disable getty is not fatal (it may not be enabled). The
    # unit also declares Conflicts=/OnFailure=getty@%i, so a kmscon that
    # cannot start hands the VT back instead of leaving it dead.
    services.disable(f"getty@tty{tty}.service")
    rc |= services.enable(f"kmsconvt@tty{tty}.service")

    if rc == 0:
        print(t("kmscon.done", tty=tty))
    return rc
