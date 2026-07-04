"""Bootloader detection and kernel parameter management.

Kernel parameters live in different places depending on the boot setup:

- UKI via mkinitcpio reads /etc/kernel/cmdline (rebuild with mkinitcpio -P)
- classic systemd-boot reads the options line of each loader entry
- GRUB reads GRUB_CMDLINE_LINUX_DEFAULT in /etc/default/grub (then needs
  grub-mkconfig)
- rEFInd reads the quoted option strings in refind_linux.conf

add_kernel_params() detects the mechanism and edits the right file
idempotently, so callers never touch /etc/kernel/cmdline directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import i18n
from .sysedit import sudo_write

t = i18n.t

CMDLINE = Path("/etc/kernel/cmdline")
SDBOOT_ENTRIES = Path("/boot/loader/entries")
GRUB_DEFAULT = Path("/etc/default/grub")
GRUB_CFG = Path("/boot/grub/grub.cfg")
REFIND_CONF = Path("/boot/refind_linux.conf")

UKI = "systemd-boot (UKI)"
SDBOOT = "systemd-boot"
GRUB = "GRUB"
REFIND = "rEFInd"
UNKNOWN = "unknown"


def detect() -> str:
    if CMDLINE.is_file():
        return UKI
    if SDBOOT_ENTRIES.is_dir() and any(SDBOOT_ENTRIES.glob("*.conf")):
        return SDBOOT
    if GRUB_CFG.is_file() and GRUB_DEFAULT.is_file():
        return GRUB
    if REFIND_CONF.is_file():
        return REFIND
    return UNKNOWN


@dataclass(frozen=True)
class ParamResult:
    changed: bool = False
    needs_mkinitcpio: bool = False  # UKI embeds the cmdline into the image
    regen_cmd: tuple[str, ...] | None = None  # e.g. grub-mkconfig


def _merge(
    existing: list[str],
    params: list[str],
    replace_prefixes: tuple[str, ...] = (),
) -> list[str] | None:
    """Existing + missing params, or None when nothing needs changing.

    Tokens matching replace_prefixes are dropped first unless they are
    exactly one of the new params — so resume=OLD gets replaced by
    resume=NEW instead of accumulating.
    """
    kept = [
        tok
        for tok in existing
        if not (
            any(tok.startswith(pfx) for pfx in replace_prefixes)
            and tok not in params
        )
    ]
    missing = [p for p in params if p not in kept]
    if not missing and kept == existing:
        return None
    return kept + missing


def _sdboot_entry_files() -> list[Path]:
    return [
        p for p in sorted(SDBOOT_ENTRIES.glob("*.conf")) if "fallback" not in p.stem
    ]


def _add_uki(params: list[str], replace: tuple[str, ...]) -> bool:
    cmdline = CMDLINE.read_text(encoding="utf-8").strip()
    merged = _merge(cmdline.split(), params, replace)
    if merged is None:
        print(t("msg.param_present", param=" ".join(params)))
        return False
    return sudo_write(CMDLINE, " ".join(merged) + "\n") == 0


def _add_sdboot(params: list[str], replace: tuple[str, ...]) -> bool:
    changed = False
    for entry in _sdboot_entry_files():
        text = entry.read_text(encoding="utf-8")
        match = re.search(r"^options[ \t]+(.*)$", text, re.MULTILINE)
        if match is None:
            new_text = f"{text.rstrip()}\noptions {' '.join(params)}\n"
        else:
            merged = _merge(match.group(1).split(), params, replace)
            if merged is None:
                continue
            new_text = f"{text[:match.start(1)]}{' '.join(merged)}{text[match.end(1):]}"
        if sudo_write(entry, new_text) == 0:
            changed = True
    if not changed:
        print(t("msg.param_present", param=" ".join(params)))
    return changed


def _add_grub(params: list[str], replace: tuple[str, ...]) -> bool:
    text = GRUB_DEFAULT.read_text(encoding="utf-8")
    match = re.search(r'^GRUB_CMDLINE_LINUX_DEFAULT="([^"]*)"', text, re.MULTILINE)
    if match is None:
        print(t("msg.grub_line_missing", path=GRUB_DEFAULT))
        return False
    merged = _merge(match.group(1).split(), params, replace)
    if merged is None:
        print(t("msg.param_present", param=" ".join(params)))
        return False
    new_text = f"{text[:match.start(1)]}{' '.join(merged)}{text[match.end(1):]}"
    return sudo_write(GRUB_DEFAULT, new_text) == 0


def _add_refind(params: list[str], replace: tuple[str, ...]) -> bool:
    changed = False
    out_lines = []
    for line in REFIND_CONF.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            parts = re.findall(r'"([^"]*)"', line)
            if len(parts) >= 2:
                merged = _merge(parts[1].split(), params, replace)
                if merged is not None:
                    line = f'"{parts[0]}" "{" ".join(merged)}"'
                    changed = True
        out_lines.append(line)
    if not changed:
        print(t("msg.param_present", param=" ".join(params)))
        return False
    return sudo_write(REFIND_CONF, "\n".join(out_lines) + "\n") == 0


def add_kernel_params(
    params: list[str], replace_prefixes: tuple[str, ...] = ()
) -> ParamResult:
    kind = detect()
    print(t("msg.bootloader", kind=kind))
    if kind == UKI:
        changed = _add_uki(params, replace_prefixes)
        return ParamResult(changed, needs_mkinitcpio=changed)
    if kind == SDBOOT:
        return ParamResult(_add_sdboot(params, replace_prefixes))
    if kind == GRUB:
        changed = _add_grub(params, replace_prefixes)
        regen = ("sudo", "grub-mkconfig", "-o", str(GRUB_CFG)) if changed else None
        return ParamResult(changed, regen_cmd=regen)
    if kind == REFIND:
        return ParamResult(_add_refind(params, replace_prefixes))
    print(t("msg.bootloader_unknown"))
    return ParamResult(False)


def current_params() -> str:
    kind = detect()
    if kind == UKI:
        return CMDLINE.read_text(encoding="utf-8").strip()
    if kind == SDBOOT:
        for entry in _sdboot_entry_files():
            match = re.search(
                r"^options[ \t]+(.*)$", entry.read_text(encoding="utf-8"), re.MULTILINE
            )
            if match:
                return match.group(1).strip()
        return ""
    if kind == GRUB:
        match = re.search(
            r'^GRUB_CMDLINE_LINUX_DEFAULT="([^"]*)"',
            GRUB_DEFAULT.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        return match.group(1) if match else ""
    if kind == REFIND:
        for line in REFIND_CONF.read_text(encoding="utf-8").splitlines():
            parts = re.findall(r'"([^"]*)"', line)
            if len(parts) >= 2:
                return parts[1]
        return ""
    return ""


def info() -> int:
    kind = detect()
    print(t("msg.bootloader", kind=kind))
    if kind == UNKNOWN:
        print(t("msg.bootloader_unknown"))
        return 1
    params = current_params()
    if params:
        print(params)
    return 0
