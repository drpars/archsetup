"""Bound the disk that systemd-coredump is allowed to consume.

MaxUse defaults to 10% of the filesystem, which on a 118 GB root is over
11 GB -- and nothing reaches that faster than a service in a crash loop.
A user unit that ran a Qt binary without a display produced 15729 dumps
in a single day here, 4.1 GB on disk, with the CPU busy compressing them.

The cap does not prevent a crash loop. It keeps one from filling the
root filesystem while it goes unnoticed, which is the part that turns a
misbehaving service into a broken system.
"""

from __future__ import annotations

from pathlib import Path

from . import i18n
from .pacman import run
from .sysedit import sudo_write

t = i18n.t

CONF_DIR = Path("/etc/systemd/coredump.conf.d")
CONF = CONF_DIR / "99-maxuse.conf"

# A drop-in rather than coredump.conf itself, so a systemd update that
# ships a new default file cannot silently drop the cap. KeepFree is left
# alone: its default (15% of the filesystem) is stricter than any fixed
# value worth hardcoding here.
CONTENT = """[Coredump]
MaxUse=1G
"""


def configure() -> int:
    rc = run(["sudo", "mkdir", "-p", str(CONF_DIR)])
    if rc != 0:
        return rc
    rc = sudo_write(CONF, CONTENT)
    if rc == 0:
        print(t("coredump.done", path=CONF))
    return rc
