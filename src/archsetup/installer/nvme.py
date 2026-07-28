"""NVMe namespace reset (installer mode, runs as root).

`nvme format` tells the controller to erase the namespace itself instead
of writing zeros through the filesystem layer. That is both faster and
more complete: it also reaches blocks the flash translation layer has
remapped out, which no `dd if=/dev/zero` can touch.

Every operation here is irreversible and covers the whole namespace, so
the confirmation asks for the device path to be typed out — a y/n prompt
is too easy to answer by reflex.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..core import i18n
from ..core.pacman import run
from .disk import guard
from .state import state

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


def list_namespaces() -> list[tuple[str, str, str]]:
    """[(node, model, size)] — parsed from `nvme list -o json`."""
    out = subprocess.run(
        ["nvme", "list", "-o", "json"], capture_output=True, text=True
    )
    if out.returncode != 0:
        return []
    try:
        devices = json.loads(out.stdout).get("Devices", [])
    except json.JSONDecodeError:
        return []

    rows = []
    for entry in devices:
        # nvme-cli 2.x nests namespaces under Subsystems/Controllers;
        # 1.x lists them flat. Accept both rather than pinning a version.
        if "DevicePath" in entry:
            rows.append((
                entry["DevicePath"],
                entry.get("ModelNumber", "?").strip(),
                _human(entry.get("PhysicalSize", 0)),
            ))
            continue
        for controller in entry.get("Subsystems", [{}]):
            for namespace in controller.get("Namespaces", []):
                rows.append((
                    f"/dev/{namespace['NameSpace']}",
                    entry.get("ModelNumber", "?").strip(),
                    _human(namespace.get("PhysicalSize", 0)),
                ))
    return rows


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024:
            return f"{value:.0f}{unit}"
        value /= 1024
    return f"{value:.0f}P"


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


# (file, label) — label None means "report the second column", the mountpoint.
IN_USE_SOURCES = (("/proc/mounts", None), ("/proc/swaps", "swap"))


def busy(dev: str) -> str | None:
    """Mountpoint or 'swap' if the namespace — or any partition of it — is in use.

    The prefix match is deliberate: /dev/nvme0n1p2 being mounted means
    the whole namespace is off limits, not just that partition.
    """
    for source, label in IN_USE_SOURCES:
        try:
            text = Path(source).read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            fields = line.split()
            if fields and fields[0].startswith(dev):
                return label or (fields[1] if len(fields) > 1 else dev)
    return None


def _forget_partitions(dev: str) -> None:
    """Drop selections pointing into a namespace that no longer has them."""
    for attr in ("bootdev", "swapdev", "rootdev", "homedev"):
        if (value := getattr(state, attr)) and value.startswith(dev):
            setattr(state, attr, None)


def reset_namespace() -> int:
    if not guard():
        return 1
    if not ensure_tool():
        return 1

    rows = list_namespaces()
    if not rows:
        print(t("nvme.none"))
        return 1

    print(f"\n{t('nvme.pick')}")
    for index, (node, model, size) in enumerate(rows, 1):
        print(f"  {index:2}) {node}  {size:>7}  {model}")
    raw = input(f"{t('inst.choice')}: ").strip()
    if not (raw.isdigit() and 1 <= int(raw) <= len(rows)):
        print(t("inst.invalid"))
        return 1
    dev = rows[int(raw) - 1][0]

    if (where := busy(dev)) is not None:
        print(t("nvme.busy", dev=dev, where=where))
        return 1

    modes = [(SES_USER, t("nvme.ses_user")), (SES_NONE, t("nvme.ses_none"))]
    if crypto_supported(dev):
        modes.insert(0, (SES_CRYPTO, t("nvme.ses_crypto")))
    else:
        print(t("nvme.no_crypto"))

    print(f"\n{t('nvme.mode_q')}")
    for index, (_, label) in enumerate(modes, 1):
        print(f"  {index}) {label}")
    raw = input(f"{t('inst.choice')} [1]: ").strip() or "1"
    if not (raw.isdigit() and 1 <= int(raw) <= len(modes)):
        print(t("inst.invalid"))
        return 1
    ses = modes[int(raw) - 1][0]

    print(f"\n{t('nvme.warning', dev=dev)}")
    if input(f"{t('nvme.confirm', dev=dev)}: ").strip() != dev:
        print(t("msg.cancelled"))
        return 1

    rc = run(["nvme", "format", "--ses", str(ses), "--force", dev])
    if rc != 0:
        return rc
    _forget_partitions(dev)
    print(t("nvme.done", dev=dev))
    return 0
