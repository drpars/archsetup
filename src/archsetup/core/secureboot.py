"""Checking that images a task just rebuilt are still signed.

Five post-install tasks rebuild boot images, and on a Secure Boot machine the
signing afterwards is somebody else's job: sbctl ships its own mkinitcpio post
hook, so a hand-run `-P` is signed too. "Somebody else does it" is a claim, and
a task that rebuilds a boot image without checking it hands the user a machine
that only reports the problem at the next power-on -- a place where nothing
here can help any more. The caller is `mkinitcpio.regenerate()` rather than
each of those five, so the check cannot be left out of the sixth.

Why the exit code is not the check. `sbctl verify` returns 0 whether or not it
found something unsigned: measured on sbctl 0.18 against a deliberately
unsigned binary in a throwaway file database, the run printed
`✗ ... is not signed` and still exited 0. `--json` is the only answer that
distinguishes the two -- a list of `{"file_name", "is_signed"}`, with 0 for
unsigned, and unsigned entries are present in it rather than omitted.

Why only our own paths. `sbctl verify` reports everything it finds on the ESP,
including files no archsetup task ever touched, which is why the installer
prints its verify as information and does not act on it. Here the paths are
known -- the presets just named them -- so the answer is narrowed to those and
an unrelated stale binary elsewhere on the ESP cannot fail a task that had
nothing to do with it. A produced path sbctl does not mention is left alone:
that is what a plain /boot/initramfs-*.img looks like, and it is not signed on
any setup. When *none* of the produced images is mentioned the silence is the
finding, and it is reported.

The whole module is gated on Secure Boot actually being on, read from
efivarfs the same way the installer reads setup mode. With it off, unsigned
images are the normal state and there is nothing to warn about.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from . import i18n

t = i18n.t

SECURE_BOOT_VAR = Path(
    "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
)
TOOL = "sbctl"


def enabled() -> bool | None:
    """Secure Boot state; None when it cannot be read.

    Straight from efivarfs -- a 4-byte attribute header followed by the value
    -- so no efivar/efitools binary is required. None is not False: a machine
    without efivarfs (a container, a BIOS box) has not answered the question.
    """
    try:
        raw = SECURE_BOOT_VAR.read_bytes()
    except OSError:
        return None
    return bool(raw[4]) if len(raw) > 4 else None


def report() -> dict[Path, bool] | None:
    """{path: is it signed} as sbctl sees it, or None when it cannot be asked.

    sudo, because sbctl refuses to run as anyone else -- it needs the key
    directory and an ESP that is mode 0700 on any sane install.
    """
    if shutil.which(TOOL) is None:
        return None
    try:
        out = subprocess.run(
            ["sudo", TOOL, "verify", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        entries = json.loads(out.stdout)
    except ValueError:
        return None
    if not isinstance(entries, list):
        return None
    signed: dict[Path, bool] = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("file_name"):
            signed[Path(entry["file_name"])] = bool(entry.get("is_signed"))
    return signed


def verify(paths: Sequence[Path]) -> int:
    """0 when the rebuilt images are signed or the question does not apply.

    1 only on a measured negative: sbctl was asked and named one of these
    files as unsigned. Everything it could not establish -- no sbctl, no
    readable answer, no mention of our images -- is printed and returns 0,
    because a task that reports failure for something it never measured
    teaches the user to stop reading its exit code.
    """
    if not paths or enabled() is not True:
        return 0

    signed = report()
    if signed is None:
        print(t("secureboot.no_sbctl" if shutil.which(TOOL) is None
                else "secureboot.unreadable"))
        return 0

    ours = {path: signed[path] for path in paths if path in signed}
    if not ours:
        print(t("secureboot.not_listed", paths=" ".join(str(p) for p in paths)))
        return 0

    unsigned = sorted(path for path, ok in ours.items() if not ok)
    if unsigned:
        print(t("secureboot.unsigned", paths=" ".join(str(p) for p in unsigned)))
        return 1
    print(t("secureboot.signed", count=len(ours)))
    return 0
