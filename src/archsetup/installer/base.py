"""Live-environment preparation and base system installation (pacstrap)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ..core import i18n, mirrors
from ..core.pacman import run
from ..core.prompt import ask_yes
from . import disk
from .state import state

t = i18n.t

MNT = Path("/mnt")
KERNELS = ("linux-zen", "linux", "linux-lts", "linux-hardened", "linux-g14")
DEFAULT_KEYMAP = "trq"


def set_live_keymap(keymap: str | None = None) -> int:
    if keymap is None:
        keymap = (
            input(f"{t('inst.keymap_q')} [{DEFAULT_KEYMAP}]: ").strip()
            or DEFAULT_KEYMAP
        )
    return run(["loadkeys", keymap])


def run_reflector() -> int:
    rc = run(["pacman", "-Sy", "--needed", "--noconfirm", "reflector"])
    if rc != 0:
        return rc
    try:
        country = input(f"{t('inst.reflector_country_q')} ").strip()
    except EOFError:
        country = ""
    # Not thorough here: timing every mirror costs more than the faster
    # mirror saves over a single pacstrap. See core.mirrors.
    return mirrors.rank(run, "/etc/pacman.d/mirrorlist", country)


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

    # linux-g14 lives in the asus-linux repo, and pacstrap resolves against
    # the *live* pacman.conf — without [g14] here the package is simply
    # "target not found" and the whole pacstrap step fails.
    if kernel == G14_KERNEL and not has_g14(LIVE_PACMAN_CONF):
        print(t("inst.g14_needed"))
        if not ask_yes(t("inst.g14_add_q")):
            print(t("inst.g14_refused"))
            return 1
        if add_g14_repo_live() != 0:
            return 1

    packages = ["base", "base-devel", "terminus-font", kernel]
    if ask_yes(t("inst.headers_q")):
        packages.append(f"{kernel}-headers")
    if ask_yes(t("inst.firmware_q")):
        packages.append("linux-firmware")
    packages.extend(pkg for pkg in state.fs_packages if pkg not in packages)

    rc = run(["pacstrap", str(MNT), *packages])
    if rc == 0:
        state.kernel = kernel
        if kernel == G14_KERNEL:
            # The installed system needs the repo too, or its kernel has
            # no source for updates.
            rc |= add_g14_repo()
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
G14_REPO = "\n[g14]\nServer = https://arch.asus-linux.org\n"
G14_KERNEL = "linux-g14"
LIVE_PACMAN_CONF = Path("/etc/pacman.conf")


def has_g14(conf: Path) -> bool:
    try:
        return "[g14]" in conf.read_text(encoding="utf-8")
    except OSError:
        return False


def _add_g14(conf: Path, prefix: list[str]) -> int:
    """Add [g14] to one pacman.conf.

    `prefix` is the arch-chroot invocation for the target, or empty to
    act on the live environment. The repo line is written only after the
    key is trusted — appending it first would make every later `pacman
    -Sy` fail on an unknown signature, including pacstrap's own.
    """
    if has_g14(conf):
        print(t("virt.already", path=conf))
        return 0
    rc = run([*prefix, "pacman-key", "--recv-keys", G14_KEY])
    rc |= run([*prefix, "pacman-key", "--lsign-key", G14_KEY])
    if rc != 0:
        print(t("inst.g14_key_failed"))
        return rc
    with open(conf, "a", encoding="utf-8") as fh:
        fh.write(G14_REPO)
    print(f"{conf} <- [g14]")
    return run([*prefix, "pacman", "-Sy"])


def add_g14_repo() -> int:
    """Target system, so the installed machine can keep updating the kernel."""
    return _add_g14(MNT / "etc/pacman.conf", ["arch-chroot", str(MNT)])


def add_g14_repo_live() -> int:
    """Live ISO: pacstrap resolves linux-g14 against *this* pacman.conf."""
    return _add_g14(LIVE_PACMAN_CONF, [])
