"""Live-environment preparation and base system installation (pacstrap)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ..core import i18n
from ..core.pacman import run
from ..core.prompt import ask_yes
from . import disk
from .state import state

t = i18n.t

MNT = Path("/mnt")
KERNELS = ("linux-zen", "linux", "linux-lts", "linux-hardened", "linux-g14")
DEFAULT_KEYMAP = "trq"


def set_live_keymap() -> int:
    keymap = input(f"{t('inst.keymap_q')} [{DEFAULT_KEYMAP}]: ").strip() or DEFAULT_KEYMAP
    return run(["loadkeys", keymap])


def run_reflector() -> int:
    rc = run(["pacman", "-Sy", "--needed", "--noconfirm", "reflector"])
    if rc != 0:
        return rc
    return run(
        ["reflector", "--verbose", "--protocol", "https", "--latest", "10",
         "--sort", "rate", "--save", "/etc/pacman.d/mirrorlist"]
    )


def _set_parallel(path: Path, count: int) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    new_text, hits = re.subn(
        r"^#?\s*ParallelDownloads\s*=.*$",
        f"ParallelDownloads = {count}",
        text,
        flags=re.MULTILINE,
    )
    if hits == 0:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def parallel_downloads() -> int:
    raw = input(f"{t('inst.parallel_q')} [5]: ").strip() or "5"
    if not raw.isdigit() or int(raw) < 1:
        print(t("inst.invalid"))
        return 1
    for conf in (Path("/etc/pacman.conf"), MNT / "etc/pacman.conf"):
        if conf.is_file() and _set_parallel(conf, int(raw)):
            print(f"{conf}: ParallelDownloads = {raw}")
    return 0


def pacstrap_base() -> int:
    if not disk.guard():
        return 1
    if not os.path.ismount(MNT):
        print(t("inst.not_mounted"))
        return 1

    print(f"\n{t('inst.kernel_q')}")
    for index, kernel in enumerate(KERNELS, 1):
        print(f"  {index}) {kernel}")
    raw = input(f"{t('inst.choice')} [1]: ").strip() or "1"
    if not (raw.isdigit() and 1 <= int(raw) <= len(KERNELS)):
        print(t("inst.invalid"))
        return 1
    kernel = KERNELS[int(raw) - 1]

    packages = ["base", "base-devel", "terminus-font", kernel]
    if ask_yes(t("inst.headers_q")):
        packages.append(f"{kernel}-headers")
    if ask_yes(t("inst.firmware_q")):
        packages.append("linux-firmware")
    packages.extend(pkg for pkg in state.fs_packages if pkg not in packages)

    rc = run(["pacstrap", str(MNT), *packages])
    if rc == 0:
        state.kernel = kernel
    return rc


def genfstab() -> int:
    modes = {"1": ["-U"], "2": ["-L"], "3": ["-t", "PARTUUID"], "4": ["-t", "PARTLABEL"]}
    print(f"\n{t('inst.fstab_q')}\n  1) UUID\n  2) LABEL\n  3) PARTUUID\n  4) PARTLABEL")
    raw = input(f"{t('inst.choice')} [1]: ").strip() or "1"
    if raw not in modes:
        print(t("inst.invalid"))
        return 1
    out = subprocess.run(
        ["genfstab", *modes[raw], "-p", str(MNT)], capture_output=True, text=True
    )
    if out.returncode != 0:
        print(out.stderr)
        return out.returncode
    (MNT / "etc/fstab").write_text(out.stdout, encoding="utf-8")
    print(out.stdout)
    return 0


def enable_multilib() -> int:
    conf = MNT / "etc/pacman.conf"
    lines = conf.read_text(encoding="utf-8").splitlines(keepends=True)
    output, in_block, changed = [], False, False
    for line in lines:
        stripped = line.strip()
        if stripped == "#[multilib]":
            in_block, changed = True, True
            output.append(line.replace("#", "", 1))
            continue
        if in_block and stripped.startswith("#Include"):
            output.append(line.replace("#", "", 1))
            in_block = False
            continue
        output.append(line)
    if not changed:
        print(t("virt.already", path=conf))
        return 0
    conf.write_text("".join(output), encoding="utf-8")
    return run(["arch-chroot", str(MNT), "pacman", "-Sy"])


G14_KEY = "8F654886F17D497FEFE3DB448B15A6B0E9A3FA35"


def add_g14_repo() -> int:
    conf = MNT / "etc/pacman.conf"
    if "[g14]" in conf.read_text(encoding="utf-8"):
        print(t("virt.already", path=conf))
        return 0
    rc = run(["arch-chroot", str(MNT), "pacman-key", "--recv-keys", G14_KEY])
    rc |= run(["arch-chroot", str(MNT), "pacman-key", "--lsign-key", G14_KEY])
    with open(conf, "a", encoding="utf-8") as fh:
        fh.write("\n[g14]\nServer = https://arch.asus-linux.org\n")
    rc |= run(["arch-chroot", str(MNT), "pacman", "-Sy"])
    return rc
