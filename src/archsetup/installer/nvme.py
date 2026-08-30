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

**`nvme sanitize` is deliberately not implemented.** The Crucial does
advertise Block Erase, so the branch is reachable on exactly one device
in reach -- but it has never been run, and an unmeasured recipe is not
written into this repo. `--ses 1` is measured, covers the same need, and
is what the Crucial's own erase went through.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..core import i18n
from ..core.pacman import run

t = i18n.t

# Secure Erase Settings, NVMe spec Format NVM command.
SES_NONE = 0      # metadata reset, blocks deallocated
SES_USER = 1      # user data erase
SES_CRYPTO = 2    # cryptographic erase (instant: the media key is discarded)

FNA_CRYPTO_BIT = 0b100  # id-ctrl FNA bit 2: crypto erase supported


def ensure_tool() -> bool:
    if Path("/usr/bin/nvme").exists():
        return True
    print(t("nvme.installing"))
    return run(["pacman", "-Sy", "--needed", "--noconfirm", "nvme-cli"]) == 0


def crypto_supported(dev: str) -> bool:
    out = subprocess.run(
        ["nvme", "id-ctrl", "-o", "json", dev], capture_output=True, text=True
    )
    if out.returncode != 0:
        return False
    try:
        return bool(json.loads(out.stdout).get("fna", 0) & FNA_CRYPTO_BIT)
    except json.JSONDecodeError:
        return False


def modes(dev: str) -> list[tuple[int, str]]:
    """The erase settings this controller says it supports, best first.

    Crypto is offered only when the controller confirms it. The other two
    are unconditional: Format NVM itself is what `oacs` bit 1 reports, and
    a controller that does not implement it fails the command rather than
    silently doing nothing.
    """
    available = [(SES_USER, t("nvme.ses_user")), (SES_NONE, t("nvme.ses_none"))]
    if crypto_supported(dev):
        available.insert(0, (SES_CRYPTO, t("nvme.ses_crypto")))
    else:
        print(t("nvme.no_crypto"))
    return available


def format_namespace(dev: str, ses: int) -> int:
    return run(["nvme", "format", "--ses", str(ses), "--force", dev])
