"""Live-environment preparation and base system installation (pacstrap)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ..core import i18n, mirrors, repos
from ..core.pacman import run
from ..core.prompt import ask_yes
from . import disk
from .state import state

t = i18n.t

MNT = Path("/mnt")
KERNELS = (
    "linux-zen",
    "linux",
    "linux-lts",
    "linux-hardened",
    "linux-ogc",
)
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

    # An out-of-tree kernel lives in a third-party repo, and pacstrap resolves
    # against the *live* pacman.conf — without that repo here the package is
    # simply "target not found" and the whole pacstrap step fails.
    repo = kernel_repo(kernel)
    if repo is not None and not has_repo(LIVE_PACMAN_CONF, repo.name):
        print(t("inst.repo_needed", repo=repo.name, kernel=kernel))
        if not ask_yes(t("inst.repo_add_q", repo=repo.name, server=repo.server)):
            print(t("inst.repo_refused", repo=repo.name))
            return 1
        if add_kernel_repo_live(repo) != 0:
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
        if repo is not None:
            # The installed system needs the repo too, or its kernel has
            # no source for updates.
            rc |= add_kernel_repo(repo)
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


LIVE_PACMAN_CONF = Path("/etc/pacman.conf")

# Which third-party repository each out-of-tree kernel comes from. One now:
# linux-g14 and [g14] came off the offer on 2026-08-21, a month after the
# repository last published, with linux-g14 left at 7.1.4 while [ogc] carries
# linux-ogc 7.1.8 and is where asus-linux's packager moved. Offering a kernel
# whose only source has gone quiet is offering a kernel that stops getting
# security updates the moment it is installed. [g14] is still *detected* --
# see _add_repo -- because an older archsetup put it in files that are still
# out there.
KERNEL_REPOS = {
    "linux-ogc": repos.OGC,
}


def kernel_repo(kernel: str) -> repos.Repo | None:
    return KERNEL_REPOS.get(kernel)


def has_repo(conf: Path, name: str) -> bool:
    try:
        return repos.has(conf.read_text(encoding="utf-8"), name)
    except OSError:
        return False


def _add_repo(repo: repos.Repo, conf: Path, prefix: list[str]) -> int:
    """Add one third-party repository to one pacman.conf.

    `prefix` is the arch-chroot invocation for the target, or empty to
    act on the live environment. The repo line is written only after the
    key is trusted — writing it first would make every later `pacman
    -Sy` fail on an unknown signature, including pacstrap's own.
    """
    if has_repo(conf, repo.name):
        print(t("virt.already", path=conf))
        return 0
    rc = run([*prefix, "pacman-key", "--recv-keys", repo.key])
    rc |= run([*prefix, "pacman-key", "--lsign-key", repo.key])
    if rc != 0:
        print(t("inst.repo_key_failed", repo=repo.name))
        return rc
    text = conf.read_text(encoding="utf-8")
    # Above any sibling publishing the same names -- a [g14] an older install
    # left behind -- and below the official ones.
    conf.write_text(
        repos.insert(text, repo, above=(repos.OUTRANKED,)), encoding="utf-8"
    )
    print(f"{conf} <- [{repo.name}]")
    return run([*prefix, "pacman", "-Sy"])


def add_kernel_repo(repo: repos.Repo) -> int:
    """Target system, so the installed machine can keep updating the kernel."""
    return _add_repo(repo, MNT / "etc/pacman.conf", ["arch-chroot", str(MNT)])


def add_kernel_repo_live(repo: repos.Repo) -> int:
    """Live ISO: pacstrap resolves the kernel against *this* pacman.conf."""
    return _add_repo(repo, LIVE_PACMAN_CONF, [])


def add_asus_repo() -> int:
    """Menu entry: the ASUS repository, for the tools rather than for a kernel.

    [ogc] is the only ASUS repository archsetup writes. A [g14] already in the
    file is outranked, not removed: a machine still running linux-g14 would be
    left with no source for its kernel at all, and nothing here knows which
    kernel booted.
    """
    return add_kernel_repo(repos.OGC)
