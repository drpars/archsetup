"""kmscon virtual console: AUR install + per-TTY systemd unit swap."""

from __future__ import annotations

from pathlib import Path

from . import i18n, pacman, services
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

CONFIG = Path("/etc/kmscon/kmscon.conf")
CONFIG_CONTENT = """font-name=JetBrainsMono Nerd Font Mono
font-size=14
font-dpi=96
"""


def _ask_tty() -> int | None:
    try:
        raw = input(f"{t('kmscon.tty_q')} [3-6, 5]: ").strip() or "5"
    except EOFError:
        return None
    if raw.isdigit() and 3 <= int(raw) <= 6:
        return int(raw)
    print(t("kmscon.tty_invalid"))
    return None


def install() -> int:
    tty = _ask_tty()
    if tty is None:
        print(t("msg.cancelled"))
        return 1

    rc = pacman.install([], ["kmscon"])
    if rc != 0:
        return rc

    rc = run(["sudo", "mkdir", "-p", str(CONFIG.parent)])
    rc |= sudo_write(CONFIG, CONFIG_CONTENT)

    # Failure to disable getty is not fatal (it may not be enabled).
    services.disable(f"getty@tty{tty}.service")
    rc |= services.enable(f"kmsconvt@tty{tty}.service")
    rc |= run(["sudo", "localectl", "set-x11-keymap", "tr", "pc105"])

    if rc == 0:
        print(t("kmscon.done", tty=tty))
    return rc
