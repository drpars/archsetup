"""Bound the disk that systemd-coredump is allowed to consume.

MaxUse defaults to 10% of the disk but is capped at 4 GiB, so on anything
larger than ~40 GB the effective default is 4 GiB. That is still a lot to
hand to a service in a crash loop: a user unit running a Qt binary with no
display produced 15729 dumps here in a single day and sat at 4.1 GB, the
default cap, with systemd rotating dumps to stay under it.

So the default does prevent a full disk -- what it does not do is keep a
crash loop cheap. 1 GB leaves room to debug a real crash while cutting
what a runaway service can occupy, and the dumps it does keep are the
recent ones that matter.
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
