"""Hand wireless address configuration to the network manager.

iwd can configure the link itself -- DHCP, DNS, routes -- or leave that to
systemd-networkd or NetworkManager. Doing both is a conflict, and a quiet
one: every association logs

    resolve-systemd: Failed to modify the DNS entries.
    org.freedesktop.resolve1.LinkBusy: Link wlan0 is managed.

because systemd-resolved refuses DNS changes from a second party once the
link is managed. Wi-Fi still works, so the conflict tends to sit there for
months while iwd re-attempts the same failing call on every connect.

Turning EnableNetworkConfiguration off leaves iwd doing authentication and
association, and the network manager doing addressing -- the split both
projects document.

This is only safe when something else really does configure the link, so
configure() refuses to touch anything unless systemd-networkd or
NetworkManager is running. The setting takes effect when iwd restarts;
that is left to the user on purpose, because restarting iwd drops the
connection and archsetup may well be running over it.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import i18n, prompt, services
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

MAIN_CONF = Path("/etc/iwd/main.conf")
SECTION = "General"
KEY = "EnableNetworkConfiguration"
VALUE = "false"

MANAGERS = ("systemd-networkd", "NetworkManager")


def set_option(text: str, section: str, key: str, value: str) -> str:
    """Set key=value under section, preserving comments and every other line.

    A rewrite from a template would be simpler, but main.conf is a file the
    user owns; only the one option is ours to change.
    """
    line = f"{key} = {value}"
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=", re.IGNORECASE)
    section_re = re.compile(rf"^\s*\[{re.escape(section)}\]\s*$", re.IGNORECASE)
    any_section_re = re.compile(r"^\s*\[[^\]]+\]\s*$")

    lines = text.splitlines()

    # Eslesme yalnizca hedef bolumun ICINDE aranir. Bolumden bagimsiz arama,
    # ayni isimli bir anahtar baska bir bolumde duruyorsa onu degistirir;
    # iwd bu secenegi [General] altindan okudugu icin degisiklik hicbir sey
    # yapmaz ama basarili gorunur.
    in_target = False
    target_header = None
    for i, existing in enumerate(lines):
        if any_section_re.match(existing):
            in_target = bool(section_re.match(existing))
            if in_target:
                target_header = i
            continue
        if in_target and key_re.match(existing):
            lines[i] = line
            return "\n".join(lines) + "\n"

    if target_header is not None:
        lines.insert(target_header + 1, line)
        return "\n".join(lines) + "\n"

    if lines and lines[-1].strip():
        lines.append("")
    lines += [f"[{section}]", line]
    return "\n".join(lines) + "\n"


def configure() -> int:
    active = [m for m in MANAGERS if services.is_active(m)]
    if not active:
        # Without a manager on the link, iwd is the only thing that can
        # configure it and disabling that leaves the machine with no address.
        print(t("iwd.no_manager", managers=", ".join(MANAGERS)))
        if not prompt.ask_yes(t("iwd.continue_q")):
            print(t("msg.cancelled"))
            return 0

    text = MAIN_CONF.read_text(encoding="utf-8") if MAIN_CONF.is_file() else ""
    updated = set_option(text, SECTION, KEY, VALUE)
    if updated == text:
        print(t("iwd.already"))
        return 0

    rc = run(["sudo", "mkdir", "-p", str(MAIN_CONF.parent)])
    if rc != 0:
        return rc
    if MAIN_CONF.is_file():
        rc = run(["sudo", "cp", str(MAIN_CONF), f"{MAIN_CONF}.bak"])
        if rc != 0:
            return rc

    rc = sudo_write(MAIN_CONF, updated)
    if rc == 0:
        print(t("iwd.done", managers=", ".join(active) or "-"))
    return rc
