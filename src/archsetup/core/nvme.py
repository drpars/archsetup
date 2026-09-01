"""NVMe controller erase: capability probe and the Format NVM command.

This is the backend `erase.py` calls when the disk it was handed is an
NVMe one. Device choice, the in-use gate and the typed confirmation live
there, because they are the same for every disk; what is NVMe-specific
is only the question "what can this controller actually do" and the
command that does it.

`nvme format` tells the controller to erase the namespace itself instead
of writing zeros through the filesystem layer. That is both faster and
more complete: it also reaches blocks the flash translation layer has
remapped out, which no `dd if=/dev/zero` can touch. Measured end to end
in a different project on this machine's own Crucial P3 Plus:
`nvme format --ses 1` returned in 48 ms, and the whole 1,000,204,886,016
bytes read back as zero afterwards.

**Capability is asked, never assumed.** Measured 2026-08-30 on this
laptop, two NVMe drives on the same bus:

    KIOXIA EXCERIA PLUS G4   oacs 0x417  fna 0     sanicap 0
    Crucial P3 Plus          oacs 0x17   fna 0x1   sanicap 0x40000002

Same class, different answers. A recipe of the shape "it is NVMe, so
sanitize it" fails outright on the first of those: `sanicap 0` means no
sanitize operation of any kind. Neither drive offers crypto erase
(`fna` bit 2 clear on both), which is why `crypto_supported()` exists
and why the mode never appears in the menu here.

**`nvme sanitize` is deliberately not implemented, and it has now been
measured rather than merely avoided.** Block Erase ran twice on the
Crucial on 2026-09-01 (`--sanact=2`, SSTAT 0x00 -> 0x101, reading back
as zeros at five places that carried random data a moment earlier).
It works. Three things came out of running it,
and together they say the same decision for better reasons:

* **It is asynchronous.** `rc=0` came back in 0.032 s and 0.037 s while
  the drive kept working for 5.2 s and =<14.4 s; the only way to know it
  finished is to poll `nvme sanitize-log`. Every task in this repo is a
  function that returns an exit code when the work is done, so a plain
  `run()` here would tell the user "erased" mid-erase -- the failure
  class this repo keeps writing down.
* **`--ses 1` already covers the need and is faster** -- 0.30 s end to
  end through the menu, against 5.2 s at best here, with the same
  zero readback.
* **The gate could not be tested.** Offering it needs a `sanicap` check,
  and nothing in the rig can exercise it: QEMU's emulated controller
  reports `sanicap 0`, and so does the other NVMe on this machine.

So the cost is a polling loop plus an untestable capability gate, bought
for a second destructive path that does the same job more slowly.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import i18n
from .pacman import run

t = i18n.t

# Secure Erase Settings, NVMe spec Format NVM command.
SES_NONE = 0      # metadata reset, blocks deallocated
SES_USER = 1      # user data erase
SES_CRYPTO = 2    # cryptographic erase (instant: the media key is discarded)

FNA_CRYPTO_BIT = 0b100  # id-ctrl FNA bit 2: crypto erase supported


def ensure_tool(sudo: bool = False) -> bool:
    """Make `nvme` available, by the route the current mode allows.

    The two branches are not cosmetic. On the live ISO there is no synced
    package database at all, so the refresh is what makes the install
    possible, and `--noconfirm` is right because the caller already said
    yes to an erase. On an installed system `-Sy` without `-u` is a
    partial upgrade -- the one pacman invocation Arch tells you never to
    make -- and it would be made here to fetch a tool for a task the user
    might still cancel at the confirmation prompt.
    """
    if Path("/usr/bin/nvme").exists():
        return True
    print(t("nvme.installing"))
    if sudo:
        return run(["sudo", "pacman", "-S", "--needed", "nvme-cli"]) == 0
    return run(["pacman", "-Sy", "--needed", "--noconfirm", "nvme-cli"]) == 0


def crypto_supported(dev: str, sudo: bool = False) -> bool | None:
    """Does the controller advertise crypto erase? None = it was not asked.

    The three-valued return is the point, and the reason is measured
    (2026-09-01, nvme-cli 2.16, this machine). `nvme id-ctrl` needs root.
    Run as an ordinary user it exits **1**, writes its usage block to
    stderr, and writes valid JSON to stdout:

        {"error":"/dev/nvme0n1: Permission denied"}

    A two-valued reader turns all of that into `False`, and `modes()` then
    prints "this controller does not support cryptographic erase" -- a
    sentence about the drive, sourced from a question nobody got to ask.
    It is the shape that reads as a measured negative: no traceback, no
    warning on the path the user sees, and the answer even happens to be
    right on both NVMe drives here, which is exactly what would have kept
    it invisible.
    """
    prefix = ["sudo"] if sudo else []
    out = subprocess.run(
        [*prefix, "nvme", "id-ctrl", "-o", "json", dev],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    try:
        return bool(json.loads(out.stdout).get("fna", 0) & FNA_CRYPTO_BIT)
    except json.JSONDecodeError:
        return None


def modes(dev: str, sudo: bool = False) -> list[tuple[int, str]]:
    """The erase settings this controller says it supports, best first.

    Crypto is offered only when the controller confirms it. The other two
    are unconditional: Format NVM itself is what `oacs` bit 1 reports, and
    a controller that does not implement it fails the command rather than
    silently doing nothing.

    An unanswered probe is reported as unanswered rather than folded into
    the "no" branch: the two lead to the same menu, but only one of them
    is a fact about the drive.
    """
    available = [(SES_USER, t("nvme.ses_user")), (SES_NONE, t("nvme.ses_none"))]
    supported = crypto_supported(dev, sudo=sudo)
    if supported:
        available.insert(0, (SES_CRYPTO, t("nvme.ses_crypto")))
    elif supported is None:
        print(t("nvme.crypto_unknown", dev=dev))
    else:
        print(t("nvme.no_crypto"))
    return available


def format_namespace(dev: str, ses: int, sudo: bool = False) -> int:
    prefix = ["sudo"] if sudo else []
    return run([*prefix, "nvme", "format", "--ses", str(ses), "--force", dev])
